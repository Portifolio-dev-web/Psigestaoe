import base64
import os
import re
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
# Segurança (varredura, item 5): sem ENVIRONMENT o servidor agora assume
# PRODUÇÃO e exige todos os segredos. Os testes rodam como desenvolvimento
# (atribuição direta, não setdefault: um ENVIRONMENT=production exportado no
# terminal não pode mudar o resultado). Testes de produção usam monkeypatch.
os.environ["ENVIRONMENT"] = "development"
# Segurança (varredura, item 14): segredo com 32+ caracteres, como a produção exige.
os.environ.setdefault("JWT_SECRET", "route-test-secret-com-pelo-menos-32-caracteres")
# Precisa ser uma chave Fernet válida: o servidor agora valida a chave na inicialização.
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ.setdefault("FERNET_KEY", TEST_FERNET_KEY)

from database import Base, get_db
import rate_limit
import rotate_encryption_key
import server
import legal
import observability
from crypto import legacy_key_from_secret
from models import AuditLog, Patient, PatientSearchToken, Record, Session, User, UserSession
from server import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    # Escala: o /health/ready usa a engine global (não passa pelo get_db).
    # Sem esta troca, o teste abria o SQLite padrão e criava psigestao_dev.db na pasta.
    original_engine = server.engine
    server.engine = engine
    # Item 9: o limitador é global ao processo; zera entre testes para um não afetar o outro.
    rate_limit.limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as test_client:
        test_client.session_factory = session_factory
        yield test_client

    app.dependency_overrides.clear()
    server.engine = original_engine
    await engine.dispose()


@pytest.mark.anyio
async def test_all_backend_routes(client):
    email = "route.test@example.com"
    password = "Senha-segura-123"
    patient_payload = {
        "full_name": "Paciente de Teste",
        # Segurança (varredura, item 3): o CPF agora é conferido (dígitos
        # verificadores). 123.456.789-09 é válido; o antigo ...-00 não era.
        "cpf": "12345678909",
        "birth_date": "1990-01-02",
        "phone": "11999999999",
        "marital_status": "Casado(a)",
        "consent_terms": True,
    }
    record_payload = {
        "session_datetime": "2026-09-15T10:00:00Z",
        "content": "Relato inicial",
        "diagnosis": "Acompanhamento",
    }
    session_payload = {
        "patient_name": "Paciente de Teste",
        "title": "Sessao inicial",
        "start": "2026-09-16T10:00:00Z",
        "end": "2026-09-16T11:00:00Z",
        "status": "agendada",
        "notes": "Confirmar horario",
    }

    assert (await client.get("/api/patients")).status_code == 401
    assert (await client.post("/api/auth/register", json={
        "name": "Profissional de Teste",
        "email": email,
        "password": password,
        "terms_accepted": False,
    })).status_code == 400

    response = await client.post("/api/auth/register", json={
        "name": "Profissional de Teste",
        "email": email,
        "password": password,
        "terms_accepted": True,
    })
    assert response.status_code == 200
    assert response.json()["user"]["email"] == email
    # Item 7: o token não vem mais no corpo; a sessão fica no cookie httpOnly,
    # que o httpx guarda e reenvia sozinho. `headers` fica vazio de propósito.
    assert "token" not in response.json()
    headers = {}

    duplicate = await client.post("/api/auth/register", json={
        "name": "Outro Nome",
        "email": email,
        "password": password,
        "terms_accepted": True,
    })
    assert duplicate.status_code == 400

    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    assert (await client.post("/api/auth/login", json={"email": email, "password": "errada"})).status_code == 401
    assert "token" not in login.json()

    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    # Item 15: o token do webhook não vem mais no /auth/me; é gerado sob demanda.
    assert "webhook_token" not in me.json()
    webhook_token = (await client.post("/api/integrations/google-forms/token")).json()["token"]

    assert (await client.get("/api/patients", headers=headers)).json() == {"items": [], "next_cursor": None}
    patient = await client.post("/api/patients", headers=headers, json=patient_payload)
    assert patient.status_code == 200
    patient_id = patient.json()["id"]
    assert patient.json()["cpf"] == "123.456.789-09"  # gravado sempre no mesmo formato
    assert patient.json()["marital_status"] == patient_payload["marital_status"]
    assert patient.json()["consent_terms"] is True
    assert patient.json()["consent_terms_status"] == "Concordo"
    assert "TERMO DE CONSENTIMENTO LIVRE E ESCLARECIDO" in patient.json()["consent_terms_text"]

    updated_patient = await client.put(
        f"/api/patients/{patient_id}",
        headers=headers,
        json={**patient_payload, "marital_status": "Divorciado(a)", "consent_terms": False},
    )
    assert updated_patient.status_code == 200
    assert updated_patient.json()["marital_status"] == "Divorciado(a)"
    assert updated_patient.json()["consent_terms"] is False
    assert updated_patient.json()["consent_terms_status"] == "Não concordo"

    records = await client.get(f"/api/patients/{patient_id}/records", headers=headers)
    assert records.status_code == 200 and records.json() == {"items": [], "next_cursor": None, "total": 0}
    record = await client.post(f"/api/patients/{patient_id}/records", headers=headers, json=record_payload)
    assert record.status_code == 200
    record_id = record.json()["id"]
    assert record.json()["content"] == record_payload["content"]

    updated_record = await client.put(
        f"/api/records/{record_id}",
        headers=headers,
        json={**record_payload, "content": "Relato atualizado"},
    )
    assert updated_record.status_code == 200
    assert updated_record.json()["version"] == 2
    assert (await client.put("/api/records/inexistente", headers=headers, json=record_payload)).status_code == 404

    stats = await client.get("/api/dashboard/stats", headers=headers)
    assert stats.status_code == 200
    assert stats.json()["total_patients"] == 1
    assert stats.json()["total_records"] == 1

    session = await client.post("/api/sessions", headers=headers, json=session_payload)
    assert session.status_code == 200
    session_id = session.json()["id"]
    assert len((await client.get("/api/sessions", headers=headers)).json()) == 1
    changed_session = await client.put(
        f"/api/sessions/{session_id}",
        headers=headers,
        json={**session_payload, "title": "Sessao atualizada"},
    )
    assert changed_session.status_code == 200
    assert changed_session.json()["title"] == "Sessao atualizada"

    webhook_payload = {
        "patient_data": {
            **patient_payload,
            "full_name": "Paciente via Forms",
            "birth_date": "14-01-1992",
        }
    }
    assert (await client.post(
        "/api/webhook/google-forms",
        headers={"X-Webhook-Token": "invalido"},
        json=webhook_payload,
    )).status_code == 401
    webhook = await client.post(
        "/api/webhook/google-forms",
        headers={"X-Webhook-Token": webhook_token},
        json=webhook_payload,
    )
    assert webhook.status_code == 200
    assert webhook.json()["status"] == "sucesso"
    forms_patient = await client.get("/api/patients", headers=headers)
    assert forms_patient.status_code == 200
    assert next(p for p in forms_patient.json()["items"] if p["full_name"] == "Paciente via Forms")["birth_date"] == "1992-01-14"

    json_export = await client.get(f"/api/patients/{patient_id}/export?format=json", headers=headers)
    assert json_export.status_code == 200
    assert json_export.headers["content-type"].startswith("application/json")
    assert json_export.json()["prontuarios"][0]["content"] == "Relato atualizado"
    assert json_export.json()["paciente"]["consent_terms"] is False

    pdf_export = await client.get(f"/api/patients/{patient_id}/export?format=pdf", headers=headers)
    assert pdf_export.status_code == 200
    assert pdf_export.headers["content-type"] == "application/pdf"
    assert pdf_export.content.startswith(b"%PDF")

    audit = await client.get("/api/audit", headers=headers)
    assert audit.status_code == 200
    assert any(item["entity_type"] == "prontuario" for item in audit.json()["items"])

    anonymized = await client.post(f"/api/patients/{patient_id}/anonymize", headers=headers)
    assert anonymized.status_code == 200
    assert anonymized.json()["anonymized"] is True
    assert anonymized.json()["full_name"] == "Paciente Anonimizado"

    assert (await client.delete(f"/api/sessions/{session_id}", headers=headers)).json() == {"ok": True}
    assert (await client.delete("/api/sessions/inexistente", headers=headers)).status_code == 404
    # Item 12: o paciente tem prontuário dentro da guarda do CFP -> não pode ser excluído.
    assert (await client.delete(f"/api/patients/{patient_id}", headers=headers)).status_code == 409
    # O paciente do Forms não tem prontuário -> exclusão definitiva permitida.
    forms_patient_id = next(p["id"] for p in forms_patient.json()["items"] if p["full_name"] == "Paciente via Forms")
    assert (await client.delete(f"/api/patients/{forms_patient_id}", headers=headers)).json() == {"ok": True}
    assert (await client.delete(f"/api/patients/{forms_patient_id}", headers=headers)).status_code == 404

    logout = await client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}


@pytest.mark.anyio
async def test_google_auth_route_creates_user_and_session(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    id_info = {
        "email": "Google.Test@example.com",
        "email_verified": True,
        "sub": "google-sub-test",  # todo ID token real do Google tem o `sub`
        "name": "Google Test",
        "picture": "https://example.com/avatar.png",
    }
    with patch("server.id_token.verify_oauth2_token", return_value=id_info):
        response = await client.post("/api/auth/google", json={"credential": "google-id-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["auth_provider"] == "google"
    assert body["user"]["email"] == "google.test@example.com"
    assert "access_token" in response.cookies
    assert (await client.get("/api/auth/me")).status_code == 200


@pytest.mark.anyio
async def test_google_auth_route_rejects_invalid_token(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    with patch("server.id_token.verify_oauth2_token", side_effect=ValueError("invalid")):
        response = await client.post("/api/auth/google", json={"credential": "forged-token"})

    assert response.status_code == 401
    assert "access_token" not in response.cookies


@pytest.mark.anyio
async def test_google_auth_route_rejects_unverified_email(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    id_info = {"email": "unverified@example.com", "email_verified": False}
    with patch("server.id_token.verify_oauth2_token", return_value=id_info):
        response = await client.post("/api/auth/google", json={"credential": "google-id-token"})

    assert response.status_code == 403


async def _create_patient(client, email="crypto.test@example.com"):
    await client.post("/api/auth/register", json={
        "name": "Crypto Test", "email": email, "password": "Senha-segura-123", "terms_accepted": True,
    })
    response = await client.post("/api/patients", json={
        "full_name": "Paciente Cripto", "cpf": "123.456.789-09", "address": "Rua A, 1",
    })
    assert response.status_code == 200
    return response.json()["id"]


@pytest.mark.anyio
async def test_auth_cookie_is_secure_in_production(client, monkeypatch):
    # Item 1: a versão insegura de set_auth_cookie não pode mais sobrescrever esta.
    monkeypatch.setattr(server, "ENVIRONMENT", "production")
    response = await client.post("/api/auth/register", json={
        "name": "Prod", "email": "prod@example.com", "password": "Senha-segura-123", "terms_accepted": True,
    })

    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "samesite=strict" in cookie


@pytest.mark.anyio
async def test_decrypt_with_wrong_key_fails_loudly(client, monkeypatch):
    # Item 2: antes, a chave errada devolvia o texto cifrado em silêncio.
    patient_id = await _create_patient(client)
    monkeypatch.setattr(server, "_fernet", MultiFernet([Fernet(Fernet.generate_key())]))

    response = await client.get(f"/api/patients/{patient_id}")

    assert response.status_code == 500


def test_decrypt_passes_through_legacy_plaintext():
    assert server.decrypt_field("12345678900") == "12345678900"


@pytest.mark.anyio
async def test_rotation_script_migrates_legacy_jwt_derived_key(client, monkeypatch):
    # Simula o banco antigo: dados gravados com a chave derivada do JWT_SECRET.
    legacy_secret = "segredo-jwt-antigo"
    monkeypatch.setattr(server, "_fernet", MultiFernet([Fernet(legacy_key_from_secret(legacy_secret))]))
    patient_id = await _create_patient(client)

    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", new_key)
    monkeypatch.setenv("LEGACY_JWT_SECRET", legacy_secret)
    monkeypatch.setattr(rotate_encryption_key, "AsyncSessionLocal", client.session_factory)
    await rotate_encryption_key.migrate(apply=True)

    # Depois da migração, só a chave nova é necessária para ler os dados.
    monkeypatch.setattr(server, "_fernet", MultiFernet([Fernet(new_key.encode())]))
    response = await client.get(f"/api/patients/{patient_id}")

    assert response.status_code == 200
    assert response.json()["cpf"] == "123.456.789-09"
    assert response.json()["address"] == "Rua A, 1"


# ---------------------------------------------------------------------------
# Itens 4 a 11 da auditoria de segurança
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_pdf_export_escapes_user_markup(client):
    # Item 4: texto clínico com < & e uma tag <img> maliciosa não pode quebrar
    # nem ser interpretado pelo ReportLab.
    patient_id = await _create_patient(client, "pdf@example.com")
    await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": "2026-09-15T10:00:00Z",
        "content": 'Ansiedade < 3 meses & sono ruim <img src="/etc/passwd"/>',
        "diagnosis": "<b>F41</b>",
    })

    response = await client.get(f"/api/patients/{patient_id}/export?format=pdf")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


@pytest.mark.anyio
async def test_consent_term_uses_owner_name_and_crp(client, monkeypatch):
    # Item 6: o termo não pode mais citar uma psicóloga fixa no código.
    # Recurso desligado na primeira versão; este teste garante que ele volta
    # funcionando quando SHOW_PROFESSIONAL_IDENTIFICATION for ligado.
    monkeypatch.setattr(server, "SHOW_PROFESSIONAL_IDENTIFICATION", True)
    patient_id = await _create_patient(client, "termo@example.com")
    assert (await client.put("/api/auth/profile", json={"name": "Dra. Ana Souza", "crp": "abc"})).status_code == 422

    profile = await client.put("/api/auth/profile", json={"name": "Dra. Ana Souza", "crp": "06/123456"})
    assert profile.status_code == 200
    assert profile.json()["crp"] == "06/123456"

    term = (await client.get(f"/api/patients/{patient_id}")).json()["consent_terms_text"]
    assert "Dra. Ana Souza – CRP 06/123456" in term
    assert "Fernanda" not in term


@pytest.mark.anyio
async def test_bearer_header_no_longer_authenticates(client):
    # Item 7: sem token no corpo, o header Bearer deixou de ser aceito.
    await _create_patient(client, "bearer@example.com")
    stolen = client.cookies.get("access_token")
    client.cookies.clear()

    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {stolen}"})

    assert response.status_code == 401


@pytest.mark.anyio
async def test_logout_revokes_token_everywhere(client):
    # Item 8: um token copiado antes do logout precisa morrer junto com a sessão.
    await _create_patient(client, "logout@example.com")
    copied_token = client.cookies.get("access_token")

    assert (await client.post("/api/auth/logout")).status_code == 200
    client.cookies.set("access_token", copied_token)

    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.anyio
async def test_login_is_rate_limited(client):
    # Item 9: a 11ª tentativa no mesmo minuto é bloqueada com 429.
    attempts = [
        (await client.post("/api/auth/login", json={"email": "alvo@example.com", "password": "chute123"})).status_code
        for _ in range(11)
    ]

    assert attempts[:10] == [401] * 10
    assert attempts[10] == 429


@pytest.mark.anyio
@pytest.mark.parametrize("weak_password", ["curta1", "somenteletras", "1234567890", "a1" * 40])
async def test_register_rejects_weak_passwords(client, weak_password):
    # Item 10: mínimo 8, letras + números, máximo 72 bytes (limite do bcrypt).
    response = await client.post("/api/auth/register", json={
        "name": "Fraca", "email": "fraca@example.com", "password": weak_password, "terms_accepted": True,
    })

    assert response.status_code == 422


def test_cors_origins_from_env(monkeypatch):
    # Item 11: CORS_ORIGINS passa a valer; "*" só é tolerado em desenvolvimento.
    monkeypatch.setenv("CORS_ORIGINS", "https://app.exemplo.com, https://www.exemplo.com/")
    assert server._load_cors_origins() == ["https://app.exemplo.com", "https://www.exemplo.com"]

    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert server._load_cors_origins() == server.DEV_CORS_ORIGINS

    monkeypatch.setattr(server, "ENVIRONMENT", "production")
    with pytest.raises(SystemExit):
        server._load_cors_origins()


# ---------------------------------------------------------------------------
# Conformidade: itens 12 a 16, termo por profissional e Política de Privacidade
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_records_under_retention_block_deletion_but_allow_archiving(client):
    # Item 12: prontuário dentro dos 5 anos do CFP não pode ser apagado.
    patient_id = await _create_patient(client, "guarda@example.com")
    await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": "2026-09-15T10:00:00Z", "content": "Sessão",
    })

    blocked = await client.delete(f"/api/patients/{patient_id}")
    assert blocked.status_code == 409
    assert "arquivar" in blocked.json()["detail"]
    retention_year = int((await client.get(f"/api/patients/{patient_id}")).json()["retention_until"][:4])
    assert retention_year == datetime.now(timezone.utc).year + 5

    # Arquivar tira da lista ativa sem apagar nada; desarquivar devolve.
    assert (await client.post(f"/api/patients/{patient_id}/archive")).json()["archived"] is True
    assert (await client.get("/api/patients")).json()["items"] == []
    assert [p["id"] for p in (await client.get("/api/patients?archived=true")).json()["items"]] == [patient_id]
    assert (await client.post(f"/api/patients/{patient_id}/unarchive")).json()["archived"] is False

    # Vencida a guarda, a exclusão definitiva é liberada.
    six_years_ago = datetime.now(timezone.utc) - timedelta(days=6 * 366)
    async with client.session_factory() as db:
        await db.execute(update(Record).where(Record.patient_id == patient_id).values(
            created_at=six_years_ago, updated_at=six_years_ago, session_datetime=six_years_ago,
        ))
        await db.commit()
    assert (await client.delete(f"/api/patients/{patient_id}")).json() == {"ok": True}


@pytest.mark.anyio
async def test_patient_and_agenda_identifiers_are_encrypted_at_rest(client):
    # Item 13: o que identifica o paciente não pode ficar legível no banco.
    patient_id = await _create_patient(client, "cripto13@example.com")
    await client.put(f"/api/patients/{patient_id}", json={
        "full_name": "Maria Sigilosa", "phone": "11988887777", "email": "maria@example.com",
        "emergency_contact": "Joao 11977776666", "initial_notes": "Queixa de ansiedade",
    })
    await client.post("/api/sessions", json={
        "title": "Sessão com Maria Sigilosa", "patient_name": "Maria Sigilosa",
        "start": "2026-10-01T10:00:00Z", "notes": "Primeira sessão",
    })

    async with client.session_factory() as db:
        p = (await db.execute(select(Patient).where(Patient.id == patient_id))).scalar_one()
        s = (await db.execute(select(Session))).scalar_one()
        details = [log.detail for log in (await db.execute(select(AuditLog))).scalars()]
    for column in ("full_name", "phone", "email", "emergency_contact", "initial_notes"):
        assert getattr(p, column).startswith("gAAAAA"), column
    for column in ("title", "patient_name", "notes"):
        assert getattr(s, column).startswith("gAAAAA"), column
    assert not any("Maria" in d or "Paciente Cripto" in d for d in details)

    # Para o profissional, nada muda: a API devolve os valores legíveis.
    assert (await client.get(f"/api/patients/{patient_id}")).json()["full_name"] == "Maria Sigilosa"
    assert (await client.get("/api/sessions")).json()[0]["title"] == "Sessão com Maria Sigilosa"


@pytest.mark.anyio
async def test_rotation_script_encrypts_legacy_plaintext_fields(client, monkeypatch):
    # Item 13: dados gravados em claro antes da mudança são criptografados pelo script.
    patient_id = await _create_patient(client, "legado@example.com")
    async with client.session_factory() as db:
        await db.execute(update(Patient).where(Patient.id == patient_id).values(
            full_name="Nome Em Claro", phone="11999990000",
        ))
        await db.commit()
    # Antes do script, o servidor ainda lê o valor legado (nada quebra no meio).
    assert (await client.get(f"/api/patients/{patient_id}")).json()["full_name"] == "Nome Em Claro"

    monkeypatch.setattr(rotate_encryption_key, "AsyncSessionLocal", client.session_factory)
    await rotate_encryption_key.migrate(apply=True)

    async with client.session_factory() as db:
        p = (await db.execute(select(Patient).where(Patient.id == patient_id))).scalar_one()
    assert p.full_name.startswith("gAAAAA") and p.phone.startswith("gAAAAA")
    assert (await client.get(f"/api/patients/{patient_id}")).json()["full_name"] == "Nome Em Claro"


@pytest.mark.anyio
async def test_audit_trail_logs_reads_with_ip_and_is_append_only(client):
    # Item 14: leituras auditadas, IP registrado, trilha imutável e sem CASCADE.
    patient_id = await _create_patient(client, "trilha@example.com")
    await client.get(f"/api/patients/{patient_id}")
    await client.get(f"/api/patients/{patient_id}/records")

    logs = (await client.get("/api/audit")).json()["items"]
    views = {(log["entity_type"], log["action"]) for log in logs}
    assert ("paciente", "visualizar") in views
    assert ("prontuario", "visualizar") in views
    assert all(log["ip_address"] == "127.0.0.1" for log in logs)

    assert not AuditLog.__table__.foreign_keys
    async with client.session_factory() as db:
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text("UPDATE audit_logs SET detail = 'adulterado'"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text("DELETE FROM audit_logs"))


@pytest.mark.anyio
async def test_webhook_token_is_hashed_shown_once_and_rotatable(client):
    # Item 15: só o hash no banco, token visível uma vez, troca revoga o anterior.
    await _create_patient(client, "webhook@example.com")
    assert (await client.get("/api/integrations/google-forms")).json()["configured"] is False

    first = (await client.post("/api/integrations/google-forms/token")).json()["token"]
    async with client.session_factory() as db:
        user = (await db.execute(select(User).where(User.email == "webhook@example.com"))).scalar_one()
    assert user.webhook_token_hash and first not in user.webhook_token_hash
    status = (await client.get("/api/integrations/google-forms")).json()
    assert status["configured"] is True and status["hint"] == first[-4:] and "token" not in status

    payload = {"patient_data": {"full_name": "Via Forms"}}

    async def send(token):
        return await client.post("/api/webhook/google-forms", headers={"X-Webhook-Token": token}, json=payload)

    assert (await send(first)).status_code == 200

    second = (await client.post("/api/integrations/google-forms/token")).json()["token"]
    assert (await send(first)).status_code == 401
    assert (await send(second)).status_code == 200

    await client.delete("/api/integrations/google-forms/token")
    assert (await send(second)).status_code == 401


@pytest.mark.anyio
async def test_google_signup_requires_explicit_policy_acceptance(client, monkeypatch):
    # Item 16: conta Google não nasce mais com aceite automático.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    id_info = {"email": "novo.google@example.com", "email_verified": True, "name": "Novo", "sub": "google-sub-novo"}
    with patch("server.id_token.verify_oauth2_token", return_value=id_info):
        user = (await client.post("/api/auth/google", json={"credential": "token"})).json()["user"]

    assert user["terms_accepted"] is False
    assert (await client.get("/api/patients")).status_code == 403
    assert (await client.post("/api/auth/accept-terms", json={"version": "1999-01-01"})).status_code == 409

    accepted = (await client.post("/api/auth/accept-terms", json={"version": legal.PRIVACY_POLICY_VERSION})).json()
    assert accepted["terms_accepted"] is True and accepted["terms_accepted_at"]
    assert (await client.get("/api/patients")).status_code == 200
    logs = (await client.get("/api/audit")).json()["items"]
    assert any(log["action"] == "aceitar" and legal.PRIVACY_POLICY_VERSION in log["detail"] for log in logs)


@pytest.mark.anyio
async def test_new_policy_version_requires_new_acceptance(client, monkeypatch):
    await _create_patient(client, "versao@example.com")
    monkeypatch.setattr(legal, "PRIVACY_POLICY_VERSION", "2099-01-01")

    assert (await client.get("/api/auth/me")).json()["terms_accepted"] is False
    assert (await client.get("/api/patients")).status_code == 403


@pytest.mark.anyio
async def test_privacy_policy_is_public(client):
    response = await client.get("/api/legal/privacy-policy")

    assert response.status_code == 200
    assert response.json()["version"] == legal.PRIVACY_POLICY_VERSION
    titles = [section["title"] for section in response.json()["sections"]]
    assert any("Direitos" in t for t in titles) and any("Encarregado" in t for t in titles)


@pytest.mark.anyio
async def test_privacy_policy_has_no_personal_data_in_code(client, monkeypatch):
    # Segurança (varredura, item 12): nome e CPF reais chegaram a ser escritos
    # no legal.py, que vai para o git e é servido sem login. Sem as variáveis
    # PRIVACY_*, o texto público só pode ter o marcador neutro, e nenhum CPF.
    for var in ("PRIVACY_CONTROLLER_NAME", "PRIVACY_CONTROLLER_DOCUMENT", "PRIVACY_DPO_NAME", "PRIVACY_DPO_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    policy = (await client.get("/api/legal/privacy-policy")).json()

    assert set(policy["controller"].values()) == {"[A PREENCHER]"}
    full_text = " ".join(p for section in policy["sections"] for p in section["paragraphs"])
    assert not re.search(r"\d{3}\.\d{3}\.\d{3}[-.]\d{2}", full_text)

    # Com as variáveis definidas (fora do git), os dados reais aparecem.
    monkeypatch.setenv("PRIVACY_CONTROLLER_NAME", "Clínica Exemplo Ltda")
    monkeypatch.setenv("PRIVACY_CONTROLLER_DOCUMENT", "00.000.000/0001-00")
    policy = (await client.get("/api/legal/privacy-policy")).json()
    assert "Clínica Exemplo Ltda (CNPJ/CPF 00.000.000/0001-00)" in policy["sections"][0]["paragraphs"][0]


def test_environment_defaults_to_production(monkeypatch):
    # Segurança (varredura, item 5): só um "development" explícito liga o modo
    # de desenvolvimento. Ausente, vazio ou com erro de digitação = produção.
    import environment

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert environment.get_environment() == "production"
    for value, expected in (("", "production"), ("prod", "production"), ("dev", "production"),
                            ("production", "production"), ("development", "development"),
                            (" Development ", "development")):
        monkeypatch.setenv("ENVIRONMENT", value)
        assert environment.get_environment() == expected, value


def test_database_url_is_required_in_production(monkeypatch):
    # Segurança (varredura, item 5): sem DATABASE_URL o sistema caía num SQLite
    # local até em produção. Agora só o desenvolvimento aceita esse fallback.
    import database

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert database.resolve_database_url() == database.DEV_SQLITE_URL

    monkeypatch.delenv("ENVIRONMENT")
    with pytest.raises(SystemExit):
        database.resolve_database_url()

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/x")
    assert database.resolve_database_url() == "postgresql+asyncpg://u:p@db/x"


@pytest.mark.anyio
async def test_api_docs_only_outside_production(client):
    # Segurança (varredura, item 12): /docs, /redoc e /openapi.json mostravam o
    # mapa completo da API a qualquer visitante. No desenvolvimento continuam;
    # em produção as três rotas somem (404).
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 200, path

    from fastapi import FastAPI

    production_app = FastAPI(**server.api_docs_urls("production"))
    transport = httpx.ASGITransport(app=production_app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as prod_client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await prod_client.get(path)).status_code == 404, path


@pytest.mark.anyio
async def test_consent_keeps_exact_copy_of_the_accepted_term(client, monkeypatch):
    # Termo por profissional: texto próprio + evidência imutável do que foi aceito.
    monkeypatch.setattr(server, "SHOW_PROFESSIONAL_IDENTIFICATION", True)  # ver teste acima
    await _create_patient(client, "consent@example.com")
    await client.put("/api/auth/profile", json={"name": "Dra. Original", "crp": "06/111111"})
    await client.put("/api/consent-term", json={"body": "Cláusula personalizada: sessões online."})

    created = (await client.post("/api/patients", json={"full_name": "Com Termo", "consent_terms": True})).json()
    assert created["consent_given_at"]
    assert "Dra. Original – CRP 06/111111" in created["consent_terms_text"]
    assert "Cláusula personalizada" in created["consent_terms_text"]

    # Mudanças posteriores do profissional não reescrevem o que o paciente aceitou.
    await client.put("/api/auth/profile", json={"name": "Dra. Nova", "crp": "06/222222"})
    await client.put("/api/consent-term", json={"body": ""})
    patient_url = "/api/patients/" + created["id"]
    assert (await client.get(patient_url)).json()["consent_terms_text"] == created["consent_terms_text"]
    assert (await client.get("/api/consent-term")).json()["is_default"] is True

    # A revogação fica registrada com data; o texto exibido volta a ser o atual.
    revoked = (await client.put(patient_url, json={"full_name": "Com Termo", "consent_terms": False})).json()
    assert revoked["consent_revoked_at"]
    assert "Dra. Nova" in revoked["consent_terms_text"]
    actions = {log["action"] for log in (await client.get("/api/audit")).json()["items"]}
    assert {"consentir", "revogar"} <= actions


# ---------------------------------------------------------------------------
# Escala: paginação, índices/busca, event loop, workers e monitoramento
# ---------------------------------------------------------------------------

async def _register(client, email):
    response = await client.post("/api/auth/register", json={
        "name": "Escala", "email": email, "password": "Senha-segura-123", "terms_accepted": True,
    })
    assert response.status_code == 200


async def _collect_pages(fetch_page):
    """Percorre todas as páginas seguindo next_cursor; devolve (itens, nº de páginas)."""
    items, cursor, pages = [], None, 0
    while True:
        page = await fetch_page(cursor)
        items += page["items"]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            return items, pages


@pytest.mark.anyio
async def test_patient_list_is_paginated_by_cursor_without_gaps_or_repeats(client):
    await _register(client, "paginas@example.com")
    created = []
    for i in range(5):
        created.append((await client.post("/api/patients", json={"full_name": f"Paciente {i}"})).json()["id"])

    first = (await client.get("/api/patients", params={"limit": 2})).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    # Item enxuto: a lista não carrega mais termo, CPF, endereço etc.
    assert set(first["items"][0]) == {"id", "full_name", "birth_date", "last_consultation_date", "anonymized", "archived"}

    # Um cadastro novo ENTRE uma página e outra não faz o cursor repetir nem pular itens.
    await client.post("/api/patients", json={"full_name": "Chegou depois"})

    async def fetch(cursor):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        return (await client.get("/api/patients", params=params)).json()

    rest, _ = await _collect_pages(lambda cursor: fetch(cursor or first["next_cursor"]))
    ids = [p["id"] for p in first["items"] + rest]
    assert ids == list(reversed(created))  # mais recentes primeiro, cada um uma vez

    assert (await client.get("/api/patients", params={"cursor": "lixo"})).status_code == 400
    assert (await client.get("/api/patients", params={"limit": 1000})).status_code == 422


@pytest.mark.anyio
async def test_server_side_name_search_uses_blind_index(client):
    await _register(client, "busca@example.com")
    ids = {}
    for name in ("Maria Silva", "Marcos Pereira", "José Souza"):
        ids[name] = (await client.post("/api/patients", json={"full_name": name})).json()["id"]

    async def search(q):
        response = await client.post("/api/patients/search", json={"q": q})
        assert response.status_code == 200
        return sorted(p["full_name"] for p in response.json()["items"])

    assert await search("mar") == ["Marcos Pereira", "Maria Silva"]       # prefixo
    assert await search("mar sil") == ["Maria Silva"]                    # todas as palavras
    assert await search("JOSE") == ["José Souza"]                        # sem acento/maiúscula
    assert await search("ilva") == []                                    # só prefixo de palavra

    # O índice acompanha edição e anonimização.
    await client.put(f"/api/patients/{ids['Marcos Pereira']}", json={"full_name": "Pedro Pereira"})
    assert await search("mar") == ["Maria Silva"]
    await client.post(f"/api/patients/{ids['Maria Silva']}/anonymize")
    assert await search("maria") == []

    # O índice não guarda nomes, só HMACs.
    async with client.session_factory() as db:
        tokens = (await db.execute(select(PatientSearchToken.token))).scalars().all()
    assert tokens and not any("maria" in t or "jose" in t for t in tokens)

    # Isolamento: outro profissional não acha os pacientes deste.
    await _register(client, "outro.consultorio@example.com")
    assert await search("jose") == []


@pytest.mark.anyio
async def test_records_are_paginated_with_total(client):
    patient_id = await _create_patient(client, "evolucao@example.com")
    for day in range(1, 6):
        await client.post(f"/api/patients/{patient_id}/records", json={
            "session_datetime": f"2026-09-0{day}T10:00:00Z", "content": f"Sessão do dia {day}",
        })

    async def fetch(cursor):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await client.get(f"/api/patients/{patient_id}/records", params=params)).json()
        assert page["total"] == 5
        return page

    records, pages = await _collect_pages(fetch)
    assert pages == 3
    assert [r["content"] for r in records] == [f"Sessão do dia {d}" for d in (5, 4, 3, 2, 1)]


@pytest.mark.anyio
async def test_audit_is_paginated(client):
    patient_id = await _create_patient(client, "auditoria.paginada@example.com")
    for _ in range(3):
        await client.get(f"/api/patients/{patient_id}")

    async def fetch(cursor):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        return (await client.get("/api/audit", params=params)).json()

    logs, pages = await _collect_pages(fetch)
    assert pages > 1
    assert len({log["log_id"] for log in logs}) == len(logs)  # sem repetições


@pytest.mark.anyio
async def test_agenda_is_fetched_by_period(client):
    await _register(client, "agenda.periodo@example.com")
    for start in ("2026-01-10T10:00:00Z", "2026-03-10T10:00:00Z"):
        await client.post("/api/sessions", json={"title": "Sessão", "start": start})

    january = await client.get("/api/sessions", params={"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T00:00:00Z"})
    assert [s["start"][:10] for s in january.json()] == ["2026-01-10"]

    upcoming = await client.get("/api/sessions", params={"start": "2026-02-01T00:00:00Z", "limit": 8})
    assert [s["start"][:10] for s in upcoming.json()] == ["2026-03-10"]

    backwards = {"start": "2026-02-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"}
    assert (await client.get("/api/sessions", params=backwards)).status_code == 400
    too_long = {"start": "2026-01-01T00:00:00Z", "end": "2027-12-31T00:00:00Z"}
    assert (await client.get("/api/sessions", params=too_long)).status_code == 400


@pytest.mark.anyio
async def test_bcrypt_runs_off_the_event_loop_and_login_timing_is_uniform(client, monkeypatch):
    event_loop_thread = threading.get_ident()
    hash_threads, verify_calls = [], []
    real_hash, real_verify = server.hash_password, server.verify_password

    def spy_hash(password):
        hash_threads.append(threading.get_ident())
        return real_hash(password)

    def spy_verify(password, password_hash):
        verify_calls.append((threading.get_ident(), password_hash))
        return real_verify(password, password_hash)

    monkeypatch.setattr(server, "hash_password", spy_hash)
    monkeypatch.setattr(server, "verify_password", spy_verify)

    await _register(client, "thread@example.com")
    # E-mail inexistente: o bcrypt roda mesmo assim (hash descartável), para o
    # tempo de resposta não revelar quais e-mails têm conta.
    await client.post("/api/auth/login", json={"email": "ninguem@example.com", "password": "Senha-123456"})

    assert hash_threads and hash_threads[0] != event_loop_thread
    assert verify_calls and verify_calls[0][0] != event_loop_thread
    assert verify_calls[0][1] == server._DUMMY_PASSWORD_HASH


@pytest.mark.anyio
async def test_pdf_is_rendered_off_the_event_loop(client, monkeypatch):
    patient_id = await _create_patient(client, "pdf.thread@example.com")
    event_loop_thread = threading.get_ident()
    render_threads = []
    real_render = server._render_patient_pdf

    def spy_render(*args):
        render_threads.append(threading.get_ident())
        return real_render(*args)

    monkeypatch.setattr(server, "_render_patient_pdf", spy_render)
    response = await client.get(f"/api/patients/{patient_id}/export", params={"format": "pdf"})

    assert response.content.startswith(b"%PDF")
    assert render_threads and render_threads[0] != event_loop_thread


def test_google_certificates_are_cached_between_logins():
    calls = []

    class FakeResponse:
        status = 200
        headers = {"cache-control": "public, max-age=600"}
        data = b"{}"

    transport = server.CachedGoogleCertsRequest()
    transport._inner = lambda url, **kwargs: calls.append(url) or FakeResponse()

    transport("https://www.googleapis.com/oauth2/v1/certs")
    transport("https://www.googleapis.com/oauth2/v1/certs")

    assert calls == ["https://www.googleapis.com/oauth2/v1/certs"]


@pytest.mark.anyio
async def test_login_purges_expired_sessions(client):
    await _register(client, "sessoes@example.com")
    async with client.session_factory() as db:
        user = (await db.execute(select(User).where(User.email == "sessoes@example.com"))).scalar_one()
        db.add(UserSession(token_hash="vencida", user_id=user.user_id,
                           expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
        await db.commit()

    await client.post("/api/auth/login", json={"email": "sessoes@example.com", "password": "Senha-segura-123"})

    async with client.session_factory() as db:
        tokens = (await db.execute(select(UserSession.token_hash))).scalars().all()
    assert "vencida" not in tokens and len(tokens) == 2  # as do cadastro e do login


class _FakeRedisPipeline:
    def __init__(self, store, fail):
        self.store, self.fail, self.ops = store, fail, []

    async def __aenter__(self):
        if self.fail:
            raise ConnectionError("redis fora do ar")
        return self

    async def __aexit__(self, *exc):
        return False

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, seconds):
        self.ops.append(("expire", key))

    async def execute(self):
        results = []
        for op, key in self.ops:
            if op == "incr":
                self.store[key] = self.store.get(key, 0) + 1
                results.append(self.store[key])
            else:
                results.append(True)
        return results


class _FakeRedis:
    def __init__(self, fail=False):
        self.store, self.fail = {}, fail

    def pipeline(self, transaction=True):
        return _FakeRedisPipeline(self.store, self.fail)


@pytest.mark.anyio
async def test_rate_limit_can_be_shared_through_redis(client, monkeypatch):
    # Workers: com REDIS_URL, todos os processos dividem o mesmo contador.
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis_limiter", rate_limit.RedisFixedWindowLimiter(client=fake))
    statuses = [
        (await client.post("/api/auth/login", json={"email": "alvo.redis@example.com", "password": "chute123"})).status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [401] * 10 and statuses[10] == 429
    assert any(key.startswith("psigestao:rl:login:") for key in fake.store)

    # Redis fora do ar: o login continua funcionando (fail open), só sem contagem.
    monkeypatch.setattr(rate_limit, "_redis_limiter", rate_limit.RedisFixedWindowLimiter(client=_FakeRedis(fail=True)))
    response = await client.post("/api/auth/login", json={"email": "outro.redis@example.com", "password": "chute123"})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_health_endpoints(client):
    assert (await client.get("/api/health/live")).json() == {"status": "ok"}
    ready = await client.get("/api/health/ready")
    assert ready.status_code == 200 and ready.json()["database"] == "up"


@pytest.mark.anyio
async def test_request_id_is_returned_and_validated(client):
    echoed = await client.get("/api/health/live", headers={"X-Request-ID": "lb-123.abc"})
    assert echoed.headers["x-request-id"] == "lb-123.abc"

    # Valor fora do padrão (pode ser tentativa de injetar linhas no log) é trocado.
    replaced = await client.get("/api/health/live", headers={"X-Request-ID": "x" * 100})
    assert replaced.headers["x-request-id"] != "x" * 100 and len(replaced.headers["x-request-id"]) == 32


@pytest.mark.anyio
async def test_metrics_use_route_templates_and_are_protected_in_production(client, monkeypatch):
    patient_id = await _create_patient(client, "metricas@example.com")
    await client.get(f"/api/patients/{patient_id}")

    body = (await client.get("/api/metrics")).text
    assert 'route="/api/patients/{pid}"' in body
    assert patient_id not in body  # ids reais nunca viram rótulo

    monkeypatch.setattr(server, "ENVIRONMENT", "production")
    monkeypatch.setattr(server, "METRICS_TOKEN", "")
    assert (await client.get("/api/metrics")).status_code == 404
    monkeypatch.setattr(server, "METRICS_TOKEN", "segredo-das-metricas")
    assert (await client.get("/api/metrics")).status_code == 404
    authorized = await client.get("/api/metrics", headers={"Authorization": "Bearer segredo-das-metricas"})
    assert authorized.status_code == 200


def test_sentry_events_are_scrubbed_of_patient_data():
    event = {
        "request": {
            "url": "https://api.exemplo.com/api/patients?archived=true",
            "query_string": "archived=true",
            "data": {"full_name": "Maria Sigilosa"},
            "cookies": {"access_token": "jwt"},
            "headers": {"Authorization": "Bearer x", "X-Webhook-Token": "wh_x", "User-Agent": "Firefox"},
        },
        "user": {"id": "user_1", "ip_address": "10.0.0.1", "email": "pro@example.com"},
    }

    scrubbed = observability.scrub_sentry_event(event)

    request = scrubbed["request"]
    assert "data" not in request and "cookies" not in request and "query_string" not in request
    assert request["url"] == "https://api.exemplo.com/api/patients"
    assert request["headers"]["Authorization"] == "[removido]"
    assert request["headers"]["X-Webhook-Token"] == "[removido]"
    assert request["headers"]["User-Agent"] == "Firefox"
    assert scrubbed["user"] == {"id": "user_1"}


# ---------------------------------------------------------------------------
# Produto: bloqueio por inatividade, rascunho automático e agenda
# ---------------------------------------------------------------------------

async def _set_session_activity(client, minutes_ago):
    async with client.session_factory() as db:
        await db.execute(update(UserSession).values(
            last_activity_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        ))
        await db.commit()


@pytest.mark.anyio
async def test_manual_lock_blocks_data_until_password_unlock(client):
    await _register(client, "bloqueio@example.com")
    assert (await client.post("/api/auth/lock", json={"reason": "manual"})).json() == {"locked": True}

    # Bloqueio de verdade: a API recusa os dados (423), não é só a tela.
    blocked = await client.get("/api/patients")
    assert blocked.status_code == 423 and blocked.json()["detail"]["code"] == "session_locked"
    # /auth/me continua respondendo, para o frontend mostrar a tela de bloqueio.
    me = (await client.get("/api/auth/me")).json()
    assert me["locked"] is True and me["has_password"] is True

    wrong = await client.post("/api/auth/unlock", json={"password": "errada-123"})
    assert wrong.status_code == 403 and wrong.json()["detail"]["remaining"] == 4

    ok = await client.post("/api/auth/unlock", json={"password": "Senha-segura-123"})
    assert ok.status_code == 200 and ok.json()["user"]["locked"] is False
    assert (await client.get("/api/patients")).status_code == 200

    actions = [log["action"] for log in (await client.get("/api/audit")).json()["items"]]
    assert {"bloquear", "desbloquear_falha", "desbloquear"} <= set(actions)


@pytest.mark.anyio
async def test_too_many_wrong_unlocks_end_the_session(client):
    await _register(client, "forca.bruta@example.com")
    await client.post("/api/auth/lock", json={"reason": "idle"})

    statuses = [
        (await client.post("/api/auth/unlock", json={"password": f"chute-{i}-x"})).status_code for i in range(5)
    ]

    assert statuses == [403, 403, 403, 403, 401]
    assert (await client.get("/api/auth/me")).status_code == 401  # sessão encerrada, cookie removido


@pytest.mark.anyio
async def test_server_locks_idle_session_even_without_the_browser(client):
    # Notebook que hibernou: o navegador não avisou, mas o servidor bloqueia sozinho.
    await _register(client, "ocioso@example.com")
    await _set_session_activity(client, minutes_ago=12)  # padrão: 10 min + 1 de folga

    assert (await client.get("/api/patients")).status_code == 423
    assert (await client.get("/api/auth/me")).json()["locked"] is True


@pytest.mark.anyio
async def test_activity_heartbeat_and_lock_settings(client):
    await _register(client, "atividade@example.com")
    await _set_session_activity(client, minutes_ago=8)
    assert (await client.post("/api/auth/activity")).json() == {"ok": True}
    async with client.session_factory() as db:
        last = (await db.execute(select(UserSession.last_activity_at))).scalar_one()
    assert datetime.now(timezone.utc) - server._as_utc(last) < timedelta(seconds=30)

    assert (await client.put("/api/auth/lock-settings", json={"minutes": 7})).status_code == 422
    assert (await client.put("/api/auth/lock-settings", json={"minutes": 5})).json()["lock_minutes"] == 5
    await _set_session_activity(client, minutes_ago=7)  # 5 min + 1 de folga já passou
    assert (await client.get("/api/patients")).status_code == 423


@pytest.mark.anyio
async def test_google_account_unlocks_with_google_only_if_same_email(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    same = {"email": "google.lock@example.com", "email_verified": True, "name": "G", "sub": "google-sub-lock"}
    with patch("server.id_token.verify_oauth2_token", return_value=same):
        await client.post("/api/auth/google", json={"credential": "t"})
    await client.post("/api/auth/accept-terms", json={"version": legal.PRIVACY_POLICY_VERSION})
    await client.post("/api/auth/lock", json={"reason": "manual"})
    assert (await client.get("/api/auth/me")).json()["has_password"] is False

    other = {"email": "outra.pessoa@example.com", "email_verified": True, "sub": "google-sub-outra"}
    with patch("server.id_token.verify_oauth2_token", return_value=other):
        assert (await client.post("/api/auth/unlock", json={"credential": "t"})).status_code == 403
    with patch("server.id_token.verify_oauth2_token", return_value=same):
        assert (await client.post("/api/auth/unlock", json={"credential": "t"})).status_code == 200


@pytest.mark.anyio
async def test_draft_key_is_per_user_encrypted_uncached_and_rotatable(client):
    await _register(client, "rascunho@example.com")
    first = await client.get("/api/auth/draft-key")
    assert first.headers["cache-control"] == "no-store"
    key = first.json()["key"]
    assert len(base64.b64decode(key)) == 32  # AES-256
    assert (await client.get("/api/auth/draft-key")).json()["key"] == key  # estável entre sessões

    async with client.session_factory() as db:
        stored = (await db.execute(select(User.draft_key).where(User.email == "rascunho@example.com"))).scalar_one()
    assert stored.startswith("gAAAAA") and key not in stored  # guardada criptografada

    assert (await client.delete("/api/auth/draft-key")).json() == {"ok": True}
    assert (await client.get("/api/auth/draft-key")).json()["key"] != key  # rascunhos antigos invalidados

    await _register(client, "outro.rascunho@example.com")
    assert (await client.get("/api/auth/draft-key")).json()["key"] != key


@pytest.mark.anyio
async def test_dates_without_timezone_are_rejected(client):
    # Bug corrigido: "09:00" sem fuso virava 09:00 UTC (06:00 no Brasil).
    patient_id = await _create_patient(client, "fuso@example.com")
    naive_session = await client.post("/api/sessions", json={"title": "Sessão", "start": "2026-10-02T09:00"})
    assert naive_session.status_code == 422
    naive_record = await client.post(f"/api/patients/{patient_id}/records",
                                     json={"session_datetime": "2026-10-02T09:00", "content": "x"})
    assert naive_record.status_code == 422

    aware = await client.post("/api/sessions", json={"title": "Sessão", "start": "2026-10-02T09:00:00-03:00"})
    assert aware.json()["start"].startswith("2026-10-02T12:00:00")  # gravado em UTC, mesmo instante


@pytest.mark.anyio
async def test_session_validation_and_patient_ownership(client):
    patient_id = await _create_patient(client, "agenda.regras@example.com")
    base = {"title": "Sessão", "start": "2026-10-05T13:00:00Z"}

    assert (await client.post("/api/sessions", json={**base, "status": "qualquer"})).status_code == 422
    assert (await client.post("/api/sessions", json={**base, "end": "2026-10-05T12:00:00Z"})).status_code == 422
    assert (await client.post("/api/sessions", json={**base, "title": "   "})).status_code == 422

    # O nome gravado vem do cadastro do paciente, não do que o navegador mandou.
    linked = await client.post("/api/sessions", json={**base, "patient_id": patient_id, "patient_name": "Nome falso"})
    assert linked.json()["patient_name"] == "Paciente Cripto"

    await _register(client, "outro.agenda@example.com")
    foreign = await client.post("/api/sessions", json={**base, "patient_id": patient_id})
    assert foreign.status_code == 422  # paciente de outro profissional


@pytest.mark.anyio
async def test_schedule_conflicts_require_confirmation(client):
    await _register(client, "conflito@example.com")
    first = (await client.post("/api/sessions", json={
        "title": "Primeira", "start": "2026-10-06T14:00:00Z", "end": "2026-10-06T14:50:00Z",
    })).json()

    # Sem término: assume 50 min, então 14:30 se sobrepõe à das 14:00.
    clash = await client.post("/api/sessions", json={"title": "Segunda", "start": "2026-10-06T14:30:00Z"})
    assert clash.status_code == 409
    assert [c["id"] for c in clash.json()["detail"]["conflicts"]] == [first["id"]]

    forced = await client.post("/api/sessions", json={
        "title": "Segunda", "start": "2026-10-06T14:30:00Z", "allow_conflict": True,
    })
    assert forced.status_code == 200

    # Encostada (começa quando a outra termina) não é conflito.
    touching = await client.post("/api/sessions", json={"title": "Terceira", "start": "2026-10-06T15:20:00Z"})
    assert touching.status_code == 200

    # Remarcar a própria sessão alguns minutos não conflita com ela mesma...
    moved = await client.put(f"/api/sessions/{first['id']}", json={
        "title": "Primeira", "start": "2026-10-06T13:50:00Z", "end": "2026-10-06T14:40:00Z", "allow_conflict": True,
    })
    assert moved.status_code == 200
    # ...e sessões canceladas não bloqueiam o horário.
    await client.put(f"/api/sessions/{forced.json()['id']}", json={
        "title": "Segunda", "start": "2026-10-06T14:30:00Z", "status": "cancelada",
    })
    retry = await client.put(f"/api/sessions/{first['id']}", json={
        "title": "Primeira", "start": "2026-10-06T14:00:00Z", "end": "2026-10-06T14:50:00Z",
    })
    assert retry.status_code == 200


@pytest.mark.anyio
async def test_first_version_hides_professional_name_and_crp(client, monkeypatch):
    # Produto (primeira versão): sistema só para profissionais; o nome e o CRP
    # do profissional não aparecem no termo nem no PDF.
    patient_id = await _create_patient(client, "versao1@example.com")
    await client.put("/api/auth/profile", json={"name": "Dra. Escondida", "crp": "06/999999"})

    me = (await client.get("/api/auth/me")).json()
    assert me["features"] == {"professional_identification": False}

    term = (await client.get("/api/consent-term")).json()["text"]
    assert term.startswith("TERMO DE CONSENTIMENTO LIVRE E ESCLARECIDO")
    assert "Profissional responsável" not in term and "CRP 06/999999" not in term and "Dra. Escondida" not in term
    assert "acima identificado" not in term  # o texto padrão não depende mais do cabeçalho

    patient_term = (await client.get(f"/api/patients/{patient_id}")).json()["consent_terms_text"]
    assert "Dra. Escondida" not in patient_term

    # PDF sem compressão no teste, para o texto ser legível nos bytes: a
    # verificação só vale se provar que o e-mail aparece E o nome não.
    import reportlab.rl_config as rl_config
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    pdf = (await client.get(f"/api/patients/{patient_id}/export", params={"format": "pdf"})).content
    assert b"Exportado por: versao1@example.com" in pdf
    assert b"Dra. Escondida" not in pdf


# ---------------------------------------------------------------------------
# Segurança (varredura, Fase 2): contas e tokens (itens 5, 7 e 14)
# ---------------------------------------------------------------------------

def _second_client():
    """Outro navegador/aparelho: o mesmo app, com cookies separados."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def _google(email, sub, name="Pessoa"):
    return patch("server.id_token.verify_oauth2_token",
                 return_value={"email": email, "email_verified": True, "sub": sub, "name": name})


@pytest.mark.anyio
async def test_google_login_cannot_hijack_account_precreated_with_password(client, monkeypatch):
    # Item 5: o atacante cadastra o e-mail da vítima com uma senha só dele...
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    async with _second_client() as attacker:
        await _register(attacker, "vitima@example.com")
        assert (await attacker.get("/api/auth/me")).status_code == 200

        # ...e a vítima entra pelo Google com o e-mail que é dela de verdade.
        with _google("vitima@example.com", "sub-da-vitima"):
            response = await client.post("/api/auth/google", json={"credential": "t"})
        assert response.status_code == 200
        assert response.json()["password_discarded"] is True
        assert response.json()["user"]["has_password"] is False

        # A sessão aberta do atacante morreu e a senha dele não abre mais a conta.
        assert (await attacker.get("/api/auth/me")).status_code == 401
        login = await attacker.post("/api/auth/login", json={"email": "vitima@example.com", "password": "Senha-segura-123"})
        assert login.status_code == 401
    me = (await client.get("/api/auth/me")).json()
    # O aceite da política tinha sido feito pelo atacante: não vale para o dono,
    # que precisa aceitar ele mesmo antes de ver qualquer dado.
    assert me["terms_accepted"] is False
    assert (await client.get("/api/patients")).status_code == 403


@pytest.mark.anyio
async def test_google_login_keeps_password_once_email_is_proven(client, monkeypatch):
    # Depois que o Google comprovou o e-mail, uma senha definida pelo dono não é mais descartada.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    with _google("dono@example.com", "sub-dono"):
        await client.post("/api/auth/google", json={"credential": "t"})
        defined = await client.post("/api/auth/password", json={"credential": "t", "new_password": "Nova-senha-123"})
        assert defined.status_code == 200 and defined.json()["user"]["has_password"] is True
        again = await client.post("/api/auth/google", json={"credential": "t"})
    assert again.json()["password_discarded"] is False
    login = await client.post("/api/auth/login", json={"email": "dono@example.com", "password": "Nova-senha-123"})
    assert login.status_code == 200


@pytest.mark.anyio
async def test_google_login_uses_the_fixed_google_identity(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    with _google("antigo@example.com", "sub-fixo"):
        first = (await client.post("/api/auth/google", json={"credential": "t"})).json()["user"]
    # Mesmo e-mail, OUTRA conta Google (ex.: e-mail corporativo reaproveitado): recusado.
    with _google("antigo@example.com", "sub-de-outra-pessoa"):
        assert (await client.post("/api/auth/google", json={"credential": "t"})).status_code == 409
    # A mesma conta Google, mesmo com outro e-mail, continua entrando na MESMA conta.
    with _google("email-novo@example.com", "sub-fixo"):
        second = (await client.post("/api/auth/google", json={"credential": "t"})).json()["user"]
    assert second["user_id"] == first["user_id"]
    # Token sem `sub` é recusado.
    with patch("server.id_token.verify_oauth2_token", return_value={"email": "x@example.com", "email_verified": True}):
        assert (await client.post("/api/auth/google", json={"credential": "t"})).status_code == 401


@pytest.mark.anyio
async def test_session_id_is_stored_only_as_hash(client):
    # Itens 7 e 14: o banco não guarda o ID que está dentro do cookie.
    import jwt
    from crypto import hash_token

    await _register(client, "hash@example.com")
    sid = jwt.decode(client.cookies["access_token"], options={"verify_signature": False})["sid"]
    async with client.session_factory() as db:
        stored = (await db.execute(select(UserSession.token_hash))).scalars().all()
    assert stored == [hash_token(sid)]
    assert sid not in stored


@pytest.mark.anyio
async def test_jwt_without_required_claims_is_rejected(client):
    # Item 14: assinatura certa não basta; as claims obrigatórias precisam estar lá.
    import jwt

    await _register(client, "claims@example.com")
    claims = jwt.decode(client.cookies["access_token"], options={"verify_signature": False})
    assert {"exp", "iat", "iss", "aud", "sub", "sid"} <= claims.keys()

    async def me_status(payload):
        forged = jwt.encode(payload, server.JWT_SECRET, algorithm="HS256")
        async with _second_client() as browser:
            return (await browser.get("/api/auth/me", headers={"Cookie": f"access_token={forged}"})).status_code

    assert await me_status(claims) == 200  # o mesmo conteúdo, reassinado: válido
    for missing in ("exp", "iat", "iss", "aud", "sub", "sid"):
        assert await me_status({k: v for k, v in claims.items() if k != missing}) == 401, missing
    assert await me_status({**claims, "aud": "outro-sistema"}) == 401
    assert await me_status({**claims, "iss": "outro-emissor"}) == 401


@pytest.mark.anyio
async def test_change_password_requires_current_and_ends_other_sessions(client):
    await _register(client, "troca@example.com")
    async with _second_client() as other_device:
        await other_device.post("/api/auth/login", json={"email": "troca@example.com", "password": "Senha-segura-123"})
        assert (await other_device.get("/api/auth/me")).status_code == 200

        def change(current, new):
            return client.post("/api/auth/password", json={"current_password": current, "new_password": new})

        assert (await change("senha-errada-1", "Outra-senha-456")).status_code == 403
        assert (await change("Senha-segura-123", "fraca")).status_code == 422  # mesma política do cadastro
        assert (await change("Senha-segura-123", "Senha-segura-123")).status_code == 400
        ok = await change("Senha-segura-123", "Outra-senha-456")
        assert ok.status_code == 200 and ok.json()["ended_sessions"] == 1
        assert (await other_device.get("/api/auth/me")).status_code == 401  # o outro aparelho caiu
    assert (await client.get("/api/auth/me")).status_code == 200  # este continua

    def login(password):
        return client.post("/api/auth/login", json={"email": "troca@example.com", "password": password})

    assert (await login("Senha-segura-123")).status_code == 401
    assert (await login("Outra-senha-456")).status_code == 200


@pytest.mark.anyio
async def test_google_only_account_defines_password_confirming_with_google(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    with _google("so.google@example.com", "sub-so-google"):
        await client.post("/api/auth/google", json={"credential": "t"})

    body = {"new_password": "Primeira-senha-1"}
    assert (await client.post("/api/auth/password", json=body)).status_code == 400  # sem confirmar identidade
    with _google("intruso@example.com", "sub-intruso"):
        assert (await client.post("/api/auth/password", json={**body, "credential": "t"})).status_code == 403
    with _google("so.google@example.com", "sub-so-google"):
        assert (await client.post("/api/auth/password", json={**body, "credential": "t"})).status_code == 200


@pytest.mark.anyio
async def test_sign_out_of_other_devices(client):
    await _register(client, "aparelhos@example.com")
    async with _second_client() as phone, _second_client() as laptop:
        for device in (phone, laptop):
            await device.post("/api/auth/login", json={"email": "aparelhos@example.com", "password": "Senha-segura-123"})
        assert (await client.get("/api/auth/sessions")).json() == {"other_sessions": 2}

        # Sem corpo JSON (como um formulário montado por outro site): recusado.
        assert (await client.post("/api/auth/sessions/end-others")).status_code == 422
        ended = await client.post("/api/auth/sessions/end-others", json={"confirm": True})
        assert ended.json() == {"ended_sessions": 2}
        assert (await phone.get("/api/auth/me")).status_code == 401
        assert (await laptop.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/sessions")).json() == {"other_sessions": 0}

    logs = (await client.get("/api/audit")).json()["items"]
    assert any(log["action"] == "encerrar" and "outros dispositivos" in log["detail"] for log in logs)


# ---------------------------------------------------------------------------
# Segurança (varredura, Fase 4): validação e exposição de informação (itens 2, 3 e 12)
# ---------------------------------------------------------------------------

def test_cpf_check_digits():
    assert server.cpf_is_valid("12345678909")
    assert not server.cpf_is_valid("12345678900")   # dígito verificador errado
    assert not server.cpf_is_valid("11111111111")   # todos iguais: passa na conta, mas não existe
    assert not server.cpf_is_valid("1234567890")    # 10 dígitos


@pytest.mark.anyio
async def test_patient_fields_are_validated(client):
    await _register(client, "validacao@example.com")

    async def create(**fields):
        return await client.post("/api/patients", json={"full_name": "Paciente Válido", **fields})

    ok = await create(cpf="123.456.789-09", phone="(11) 91234-5678", email="Paciente@Example.com")
    assert ok.status_code == 200
    assert ok.json()["cpf"] == "123.456.789-09" and ok.json()["email"] == "Paciente@example.com"

    cases = {
        "cpf": "123.456.789-00",          # dígito verificador errado
        "phone": "1234",                  # sem DDD
        "email": "nao-e-email",
        "birth_date": "2999-01-01",       # no futuro
        "full_name": "x" * 151,           # acima do limite
        "address": "Rua " * 100,          # 400 caracteres (limite 300)
        "initial_notes": "a" * 10_001,
        "profession": "Psic\x00ologa",   # caractere nulo: antes virava erro 500 no PostgreSQL
    }
    for field, value in cases.items():
        assert (await create(**{field: value})).status_code == 422, field
    assert (await create(full_name="   ")).status_code == 422

    # Mensagens em português, com o limite, sem repetir o valor enviado.
    too_long = (await create(address="Rua " * 100)).json()["detail"][0]
    assert too_long["msg"] == "Texto longo demais (máximo de 300 caracteres)."

    # Data de nascimento em branco no formulário = sem data (antes: erro 422 em inglês).
    blank_date = await create(birth_date="")
    assert blank_date.status_code == 200 and blank_date.json()["birth_date"] in ("", None)


@pytest.mark.anyio
async def test_record_and_query_limits(client):
    patient_id = await _create_patient(client, "limites@example.com")
    record = {"session_datetime": "2026-09-15T10:00:00Z", "content": "a" * 50_001}
    assert (await client.post(f"/api/patients/{patient_id}/records", json=record)).status_code == 422
    assert (await client.post(f"/api/patients/{patient_id}/records", json={**record, "content": "ok"})).status_code == 200

    assert (await client.get(f"/api/patients/{patient_id}/export", params={"format": "exe"})).status_code == 422
    assert (await client.get("/api/patients", params={"cursor": "c" * 201})).status_code == 422


@pytest.mark.anyio
async def test_webhook_keeps_limits_but_not_format_rules(client):
    # O paciente preenche o Google Forms: um CPF digitado errado não pode fazer
    # o cadastro inteiro sumir. Os limites de tamanho continuam valendo.
    await _register(client, "forms.validacao@example.com")
    token = (await client.post("/api/integrations/google-forms/token")).json()["token"]

    def send(data):
        return client.post("/api/webhook/google-forms", headers={"X-Webhook-Token": token},
                           json={"patient_data": {"full_name": "Paciente do Forms", **data}})

    accepted = await send({"cpf": "123.456.789-00", "phone": "1234", "email": "sem-arroba"})
    assert accepted.status_code == 200
    stored = (await client.get(f"/api/patients/{accepted.json()['paciente_id']}")).json()
    assert stored["cpf"] == "123.456.789-00"  # o profissional corrige ao editar
    assert (await send({"address": "x" * 301})).status_code == 422


@pytest.mark.anyio
async def test_request_body_size_is_limited(client):
    # Antes o corpo inteiro ia para a memória, sem limite, até em rotas sem login.
    huge = {"email": "a@example.com", "password": "x" * (2 * 1024 * 1024)}
    response = await client.post("/api/auth/login", json=huge)
    assert response.status_code == 413
    assert "grande demais" in response.json()["detail"]

    async def chunks():  # envio em partes, sem Content-Length
        for _ in range(3):
            yield b"x" * (600 * 1024)

    streamed = await client.post("/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert streamed.status_code == 413

    async def small_chunks():
        yield b'{"email": "nao@example.com", '
        yield b'"password": "Senha-qualquer-1"}'

    normal = await client.post("/api/auth/login", content=small_chunks(), headers={"Content-Type": "application/json"})
    assert normal.status_code == 401  # o corpo em partes chegou inteiro e foi validado normalmente


@pytest.mark.anyio
async def test_validation_error_does_not_echo_input(client):
    secret = "senhasomenteletras"  # fora da política (sem números)
    response = await client.post("/api/auth/register", json={
        "name": "Eco", "email": "eco@example.com", "password": secret, "terms_accepted": True,
    })
    assert response.status_code == 422
    assert secret not in response.text
    error = response.json()["detail"][0]
    assert set(error) == {"loc", "msg", "type"}
    assert error["loc"] == ["body", "password"]
    assert error["msg"] == "A senha deve conter letras e números."  # sem o prefixo "Value error, "


@pytest.mark.anyio
async def test_security_headers(client):
    response = await client.get("/api/health/live")
    headers = response.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in headers  # HSTS só em produção (HTTPS)

    # Rota que define o próprio Cache-Control não é sobrescrita; /docs fica sem a CSP (usa CDN).
    assert "content-security-policy" not in (await client.get("/docs")).headers

    # Erros também levam os cabeçalhos.
    assert (await client.get("/api/patients")).headers["x-frame-options"] == "DENY"

    from fastapi import FastAPI
    from http_security import SecurityHeadersMiddleware

    production_app = FastAPI()
    production_app.add_middleware(SecurityHeadersMiddleware, hsts=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=production_app), base_url="https://test") as prod:
        assert "max-age=63072000" in (await prod.get("/x")).headers["strict-transport-security"]
