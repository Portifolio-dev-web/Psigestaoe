import base64
import io
import json
import os
import re
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from datetime import date, datetime, timedelta, timezone

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
# Segurança média: os e-mails ficam numa caixa de saída em memória (mailer.outbox).
os.environ["MAIL_BACKEND"] = "memory"
# A rotina de manutenção não roda sozinha nos testes (eles chamam as funções direto).
os.environ["MAINTENANCE_ENABLED"] = "false"

from database import Base, get_db
import rate_limit
import rebuild_search_index
import rotate_encryption_key
import server
import legal
import mailer
import observability
import totp
from crypto import legacy_key_from_secret
from models import (
    AuditLog, EmailToken, Patient, PatientSearchToken, Record, RecordVersion, Session, User, UserSession,
)
from server import app


CSRF_HEADERS = {"X-Requested-With": "psigestao"}


def patient_data(name="Paciente de Teste", **overrides):
    """Ficha de paciente COMPLETA e válida. Produto: todos os campos do cadastro
    são obrigatórios, então os testes partem desta ficha e trocam só o que testam."""
    data = {
        "full_name": name, "cpf": "123.456.789-09", "rg": "12.345.678-9", "birth_date": "1990-01-02",
        "education": "Superior - Completo", "marital_status": "Solteiro(a)", "profession": "Professora",
        "phone": "(11) 91234-5678", "email": "paciente@example.com", "address": "Rua A, 1",
        "emergency_contact": "Maria (11) 99999-0000", "initial_notes": "Primeiro contato.", "consent_terms": False,
    }
    data.update(overrides)
    return data


def link_token(email):
    """Token do último link enviado a este e-mail (o link leva #token=...)."""
    for mail in reversed(mailer.outbox):
        if mail.to == email:
            match = re.search(r"#token=([A-Za-z0-9_\-]+)", mail.text)
            if match:
                return match.group(1)
    raise AssertionError(f"nenhum link enviado para {email}")


async def register_account(client, email, password="Senha-segura-123", name="Profissional de Teste"):
    """Cadastro completo (segurança média): pedido -> link no e-mail -> senha.
    Devolve a resposta da confirmação (com a sessão já aberta no cookie)."""
    response = await client.post("/api/auth/register", json={"name": name, "email": email, "terms_accepted": True})
    assert response.status_code == 200, response.text
    return await client.post("/api/auth/register/confirm", json={"token": link_token(email), "password": password})


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
    mailer.outbox.clear()
    transport = httpx.ASGITransport(app=app)
    # Varredura, item 10: como o frontend (lib/api.js), o cliente dos testes
    # envia o cabeçalho anti-CSRF; sem ele as escritas são recusadas (403).
    async with httpx.AsyncClient(transport=transport, base_url="https://test", headers=CSRF_HEADERS) as test_client:
        test_client.session_factory = session_factory
        yield test_client

    app.dependency_overrides.clear()
    server.engine = original_engine
    await engine.dispose()


@pytest.mark.anyio
async def test_all_backend_routes(client):
    email = "route.test@example.com"
    password = "Senha-segura-123"
    # Produto: ficha completa (todos os campos são obrigatórios).
    # Segurança (varredura, item 3): o CPF é conferido (dígitos verificadores).
    # 123.456.789-09 é válido; o antigo ...-00 não era.
    patient_payload = patient_data(cpf="12345678909", phone="11999999999", marital_status="Casado(a)",
                                   consent_terms=True)
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
        "terms_accepted": False,
    })).status_code == 400

    # Segurança média: cadastro em dois passos (link no e-mail + senha).
    response = await register_account(client, email, password)
    assert response.status_code == 200
    assert response.json()["user"]["email"] == email
    # Item 7: o token não vem mais no corpo; a sessão fica no cookie httpOnly,
    # que o httpx guarda e reenvia sozinho. `headers` fica vazio de propósito.
    assert "token" not in response.json()
    headers = {}

    # Segurança média: e-mail já cadastrado recebe a MESMA resposta (não revela
    # quem tem conta); o aviso "você já tem uma conta" vai só para o e-mail.
    duplicate = await client.post("/api/auth/register", json={
        "name": "Outro Nome",
        "email": email,
        "terms_accepted": True,
    })
    assert duplicate.status_code == 200 and duplicate.json()["status"] == "check_email"
    assert mailer.outbox[-1].to == email and "já tem uma conta" in mailer.outbox[-1].text

    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    assert (await client.post("/api/auth/login", json={"email": email, "password": "errada"})).status_code == 401
    assert "token" not in login.json()

    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    # Item 15: o token do webhook não vem mais no /auth/me; é gerado sob demanda.
    assert "webhook_token" not in me.json()
    webhook_token = (await client.post("/api/integrations/google-forms/token")).json()["token"]

    # Complementares: a lista traz também o total de pacientes.
    assert (await client.get("/api/patients", headers=headers)).json() == {"items": [], "next_cursor": None, "total": 0}
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
        # Varredura, item 9: a edição informa a versão que foi aberta.
        json={**record_payload, "content": "Relato atualizado", "expected_version": 1},
    )
    assert updated_record.status_code == 200
    assert updated_record.json()["version"] == 2
    assert (await client.put("/api/records/inexistente", headers=headers,
                             json={**record_payload, "expected_version": 1})).status_code == 404

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

    # Fluxo clínico (P1): a resposta informa quantas sessões saíram (série: mais de uma).
    assert (await client.delete(f"/api/sessions/{session_id}", headers=headers)).json() == {"ok": True, "deleted": 1}
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
    await register_account(client, email, name="Crypto Test")
    response = await client.post("/api/patients", json=patient_data("Paciente Cripto", address="Rua A, 1"))
    assert response.status_code == 200
    return response.json()["id"]


@pytest.mark.anyio
async def test_auth_cookie_is_secure_in_production(client, monkeypatch):
    # Item 1: a versão insegura de set_auth_cookie não pode mais sobrescrever esta.
    monkeypatch.setattr(server, "ENVIRONMENT", "production")
    response = await register_account(client, "prod@example.com", name="Prod")

    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    # Varredura, item 18: prefixo __Host- (exige Secure, Path=/ e nenhum Domain).
    assert cookie.startswith("__host-access_token=")
    assert "path=/" in cookie and "domain=" not in cookie
    # E a sessão funciona com o nome novo.
    assert (await client.get("/api/auth/me")).status_code == 200


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
    # Segurança média: a senha agora é criada na confirmação do cadastro.
    response = await client.post("/api/auth/register/confirm", json={
        "token": "x" * 43, "password": weak_password,
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
    await client.put(f"/api/patients/{patient_id}", json=patient_data(
        "Maria Sigilosa", phone="11988887777", email="maria@example.com",
        emergency_contact="Joao 11977776666", initial_notes="Queixa de ansiedade",
    ))
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

    # Segurança média: em produção a conexão com o banco precisa pedir TLS.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/x")
    monkeypatch.delenv("DATABASE_TLS_DISABLED", raising=False)
    with pytest.raises(SystemExit):
        database.resolve_database_url()
    for mode in ("require", "verify-full"):
        monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://u:p@db/x?ssl={mode}")
        assert database.resolve_database_url() == f"postgresql+asyncpg://u:p@db/x?ssl={mode}"
    # Exceção explícita: banco na mesma máquina/rede privada isolada.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/x")
    monkeypatch.setenv("DATABASE_TLS_DISABLED", "true")
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

    created = (await client.post("/api/patients", json=patient_data("Com Termo", consent_terms=True))).json()
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
    revoked = (await client.put(patient_url, json=patient_data("Com Termo", consent_terms=False))).json()
    assert revoked["consent_revoked_at"]
    assert "Dra. Nova" in revoked["consent_terms_text"]
    actions = {log["action"] for log in (await client.get("/api/audit")).json()["items"]}
    assert {"consentir", "revogar"} <= actions


# ---------------------------------------------------------------------------
# Escala: paginação, índices/busca, event loop, workers e monitoramento
# ---------------------------------------------------------------------------

async def _register(client, email):
    response = await register_account(client, email, name="Escala")
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
        created.append((await client.post("/api/patients", json=patient_data(f"Paciente {i}"))).json()["id"])

    first = (await client.get("/api/patients", params={"limit": 2})).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    # Item enxuto: a lista não carrega mais termo, CPF, endereço etc.
    assert set(first["items"][0]) == {"id", "full_name", "birth_date", "last_consultation_date", "anonymized", "archived",
                                      "next_session"}  # complementares: próxima sessão de cada paciente

    # Um cadastro novo ENTRE uma página e outra não faz o cursor repetir nem pular itens.
    await client.post("/api/patients", json=patient_data("Chegou depois"))

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
        ids[name] = (await client.post("/api/patients", json=patient_data(name))).json()["id"]

    async def search(q):
        response = await client.post("/api/patients/search", json={"q": q})
        assert response.status_code == 200
        return sorted(p["full_name"] for p in response.json()["items"])

    assert await search("mar") == ["Marcos Pereira", "Maria Silva"]       # prefixo
    assert await search("mar sil") == ["Maria Silva"]                    # todas as palavras
    assert await search("JOSE") == ["José Souza"]                        # sem acento/maiúscula
    assert await search("ilva") == []                                    # só prefixo de palavra

    # O índice acompanha edição e anonimização.
    await client.put(f"/api/patients/{ids['Marcos Pereira']}", json=patient_data("Pedro Pereira"))
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
    # Análise de UI (P6): título em branco deixou de ser erro; o servidor gera um.
    # (outro horário: esta sessão agora é criada e ocuparia o das próximas)
    untitled = await client.post("/api/sessions", json={**base, "title": "   ", "start": "2026-10-05T18:00:00Z"})
    assert untitled.status_code == 200 and untitled.json()["title"] == "Compromisso"

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
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test", headers=CSRF_HEADERS)


def _google(email, sub, name="Pessoa"):
    return patch("server.id_token.verify_oauth2_token",
                 return_value={"email": email, "email_verified": True, "sub": sub, "name": name})


@pytest.mark.anyio
async def test_google_login_cannot_hijack_account_precreated_with_password(client, monkeypatch):
    # Item 5: o atacante cadastra o e-mail da vítima com uma senha só dele...
    # Segurança média: pelo cadastro isso não é mais possível (a conta só nasce
    # com o link enviado ao e-mail). Fica o caso das contas ANTIGAS, criadas
    # antes da confirmação por e-mail: simuladas aqui direto no banco.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    async with client.session_factory() as db:
        db.add(User(email="vitima@example.com", name="Atacante", auth_provider="email",
                    password_hash=server.hash_password("Senha-segura-123"), terms_accepted=True,
                    terms_version=legal.PRIVACY_POLICY_VERSION))
        await db.commit()
    async with _second_client() as attacker:
        await attacker.post("/api/auth/login", json={"email": "vitima@example.com", "password": "Senha-segura-123"})
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
        # Segurança média: cada sessão vem com aparelho e IP; esta aparece primeiro.
        listed = (await client.get("/api/auth/sessions")).json()
        assert listed["other_sessions"] == 2 and len(listed["sessions"]) == 3
        assert listed["sessions"][0]["current"] is True

        # Sem corpo JSON (como um formulário montado por outro site): recusado.
        assert (await client.post("/api/auth/sessions/end-others")).status_code == 422
        ended = await client.post("/api/auth/sessions/end-others", json={"confirm": True})
        assert ended.json() == {"ended_sessions": 2}
        assert (await phone.get("/api/auth/me")).status_code == 401
        assert (await laptop.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/sessions")).json()["other_sessions"] == 0

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
        return await client.post("/api/patients", json=patient_data("Paciente Válido", **fields))

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

    # Data de nascimento em branco: lida como "sem data" (antes virava erro em
    # inglês) e recusada porque o campo é obrigatório, com mensagem clara.
    blank_date = await create(birth_date="")
    assert blank_date.status_code == 422
    assert "Data de nascimento" in blank_date.json()["detail"][0]["msg"]


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
    response = await client.post("/api/auth/register/confirm", json={"token": "x" * 43, "password": secret})
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


# ---------------------------------------------------------------------------
# Segurança (varredura, Fase 5): concorrência e envio duplicado (item 9)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_record_update_uses_optimistic_version_control(client):
    patient_id = await _create_patient(client, "versao.otimista@example.com")
    record_id = (await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": "2026-09-15T10:00:00Z", "content": "Versão 1"})).json()["id"]
    body = {"session_datetime": "2026-09-15T10:00:00Z", "content": "Edição da aba A"}

    # Sem informar a versão aberta: recusado (o servidor não teria como detectar conflito).
    assert (await client.put(f"/api/records/{record_id}", json=body)).status_code == 422

    assert (await client.put(f"/api/records/{record_id}", json={**body, "expected_version": 1})).json()["version"] == 2

    # A aba B ainda estava com a versão 1 aberta: recusada, e NADA muda.
    stale = await client.put(f"/api/records/{record_id}",
                             json={**body, "content": "Edição da aba B", "expected_version": 1})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "record_conflict"
    assert stale.json()["detail"]["current_version"] == 2
    current = (await client.get(f"/api/patients/{patient_id}/records")).json()["items"][0]
    assert current["content"] == "Edição da aba A" and current["version"] == 2

    # "Salvar mesmo assim": reenvia com a versão atual; a da aba A vai para o histórico.
    forced = await client.put(f"/api/records/{record_id}",
                              json={**body, "content": "Edição da aba B", "expected_version": 2})
    assert forced.json()["version"] == 3
    async with client.session_factory() as db:
        archived = (await db.execute(select(RecordVersion.version).where(RecordVersion.record_id == record_id)
                                     .order_by(RecordVersion.version))).scalars().all()
        texts = [server.decrypt_field(c) for c in (await db.execute(
            select(RecordVersion.content).where(RecordVersion.record_id == record_id)
            .order_by(RecordVersion.version))).scalars().all()]
    assert archived == [1, 2]  # cada versão arquivada UMA vez, nenhuma perdida
    assert texts == ["Versão 1", "Edição da aba A"]


@pytest.mark.anyio
async def test_unlock_attempt_is_reserved_before_checking_the_password(client, monkeypatch):
    # Antes: requisições paralelas liam o mesmo contador e todas conferiam a
    # senha. Agora cada uma reserva a tentativa no banco ANTES do bcrypt.
    # Simula uma rajada que já reservou as 5 tentativas: a próxima requisição
    # é recusada sem conferir nada, MESMO com a senha certa.
    # (A concorrência real é provada contra o Postgres; o SQLite dos testes
    # usa uma conexão só e não executa requisições de fato em paralelo.)
    await _register(client, "rajada@example.com")
    await client.post("/api/auth/lock", json={"reason": "manual"})
    async with client.session_factory() as db:
        await db.execute(update(UserSession).values(unlock_failures=server.MAX_UNLOCK_FAILURES))
        await db.commit()

    checked = []
    real_verify = server.verify_password_async

    async def counting_verify(password, password_hash):
        checked.append(password)
        return await real_verify(password, password_hash)

    monkeypatch.setattr(server, "verify_password_async", counting_verify)
    response = await client.post("/api/auth/unlock", json={"password": "Senha-segura-123"})

    assert response.status_code == 401 and response.json()["detail"]["code"] == "session_ended"
    assert checked == []  # nenhuma senha conferida além do limite
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.anyio
async def test_simultaneous_registration_with_same_email_is_not_a_500(client, monkeypatch):
    # Simula a corrida: outra requisição grava o mesmo e-mail DEPOIS da checagem
    # de duplicidade desta e ANTES de ela gravar.
    real_hash = server.hash_password_async

    async def hash_while_other_tab_registers(password):
        async with client.session_factory() as other:
            other.add(User(email="corrida@example.com", name="Outra aba", password_hash="x", terms_accepted=True))
            await other.commit()
        return await real_hash(password)

    # Segurança média: a conta nasce na CONFIRMAÇÃO (link do e-mail + senha).
    assert (await client.post("/api/auth/register", json={
        "name": "Corrida", "email": "corrida@example.com", "terms_accepted": True,
    })).status_code == 200
    monkeypatch.setattr(server, "hash_password_async", hash_while_other_tab_registers)
    response = await client.post("/api/auth/register/confirm", json={
        "token": link_token("corrida@example.com"), "password": "Senha-segura-123",
    })
    assert response.status_code == 409
    assert "já tem uma conta" in response.json()["detail"]


@pytest.mark.anyio
async def test_integrity_error_handler_hides_values():
    from sqlalchemy.exc import IntegrityError
    from http_security import integrity_error_handler

    error = IntegrityError("INSERT ...", {}, Exception('Key (email)=(fulano@example.com) already exists'))
    response = await integrity_error_handler(None, error)
    assert response.status_code == 409
    assert b"fulano" not in response.body
    assert json.loads(response.body)["detail"]["code"] == "concurrent_write"


# ---------------------------------------------------------------------------
# Segurança (varredura, Fase 6): defesas de borda (itens 8, 10, 15, 18 e 19)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_writes_require_csrf_header_and_allowed_origin(client):
    await _register(client, "csrf@example.com")
    patient_id = (await client.post("/api/patients", json=patient_data("Paciente CSRF"))).json()["id"]
    cookie = {"Cookie": f"access_token={client.cookies['access_token']}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as browser:
        # Como um formulário de OUTRO site: o cookie vai junto, mas sem o cabeçalho próprio.
        forged = await browser.post(f"/api/patients/{patient_id}/anonymize", headers=cookie)
        assert forged.status_code == 403 and forged.json()["detail"]["code"] == "csrf_rejected"

        # Com o cabeçalho, mas vindo de uma origem fora da lista: também recusado.
        foreign = await browser.post(f"/api/patients/{patient_id}/archive", headers={
            **cookie, **CSRF_HEADERS, "Origin": "https://site-malicioso.example"})
        assert foreign.status_code == 403

        # Leituras não exigem o cabeçalho.
        assert (await browser.get("/api/auth/me", headers=cookie)).status_code == 200

        # O webhook do Google Forms (sem cookie, sem Origin) continua funcionando.
        token = (await client.post("/api/integrations/google-forms/token")).json()["token"]
        hook = await browser.post("/api/webhook/google-forms", headers={"X-Webhook-Token": token},
                                  json={"patient_data": {"full_name": "Paciente do Forms"}})
        assert hook.status_code == 200

    # Origem permitida + cabeçalho: aceito. O paciente não foi anonimizado acima.
    allowed = await client.post(f"/api/patients/{patient_id}/archive", headers={"Origin": "http://localhost:3000"})
    assert allowed.status_code == 200
    assert (await client.get(f"/api/patients/{patient_id}")).json()["full_name"] == "Paciente CSRF"


@pytest.mark.anyio
async def test_cors_allows_only_used_methods_and_headers(client):
    def preflight(method, headers="content-type"):
        return client.options("/api/patients", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers})

    ok = await preflight("POST", "content-type,x-requested-with")
    assert ok.status_code == 200 and ok.headers["access-control-max-age"] == "600"
    assert (await preflight("PATCH")).status_code == 400
    assert (await preflight("POST", "x-cabecalho-qualquer")).status_code == 400
    unknown = await client.options("/api/patients", headers={
        "Origin": "https://site-malicioso.example", "Access-Control-Request-Method": "POST"})
    assert unknown.status_code == 400


def test_production_cors_requires_https(monkeypatch):
    monkeypatch.setattr(server, "ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.exemplo.com.br,http://app.exemplo.com.br")
    with pytest.raises(SystemExit):
        server._load_cors_origins()
    monkeypatch.setenv("CORS_ORIGINS", "https://app.exemplo.com.br")
    assert server._load_cors_origins() == ["https://app.exemplo.com.br"]


def test_production_requires_shared_rate_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis_limiter", None)
    monkeypatch.delenv("RATE_LIMIT_ALLOW_MEMORY", raising=False)
    rate_limit.require_shared_rate_limit("development")  # desenvolvimento: memória é aceita
    with pytest.raises(SystemExit):
        rate_limit.require_shared_rate_limit("production")

    # Exceção explícita, só com um único processo.
    monkeypatch.setenv("RATE_LIMIT_ALLOW_MEMORY", "true")
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    rate_limit.require_shared_rate_limit("production")
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(SystemExit):
        rate_limit.require_shared_rate_limit("production")

    monkeypatch.setattr(rate_limit, "_redis_limiter", rate_limit.RedisFixedWindowLimiter(client=_FakeRedis()))
    # Segurança média: o Redis de produção precisa de TLS (rediss://).
    monkeypatch.delenv("REDIS_TLS_DISABLED", raising=False)
    monkeypatch.setattr(rate_limit, "REDIS_URL", "redis://cache:6379/0")
    with pytest.raises(SystemExit):
        rate_limit.require_shared_rate_limit("production")
    monkeypatch.setenv("REDIS_TLS_DISABLED", "true")  # exceção explícita (rede privada)
    rate_limit.require_shared_rate_limit("production")
    monkeypatch.delenv("REDIS_TLS_DISABLED")
    monkeypatch.setattr(rate_limit, "REDIS_URL", "rediss://cache:6380/0")
    rate_limit.require_shared_rate_limit("production")  # com Redis e TLS: ok
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_ENABLED", False)
    with pytest.raises(SystemExit):  # desligar o limite em produção: nunca
        rate_limit.require_shared_rate_limit("production")


@pytest.mark.anyio
async def test_search_and_export_are_rate_limited_per_account(client, monkeypatch):
    patient_id = await _create_patient(client, "limites.borda@example.com")
    monkeypatch.setattr(server, "SEARCH_LIMIT_PER_MINUTE", 3)
    monkeypatch.setattr(server, "EXPORT_LIMIT", 2)

    searches = [(await client.post("/api/patients/search", json={"q": "Paciente"})).status_code for _ in range(4)]
    assert searches == [200, 200, 200, 429]

    exports = [(await client.get(f"/api/patients/{patient_id}/export")).status_code for _ in range(3)]
    assert exports == [200, 200, 429]


def test_forwarded_allow_ips_is_validated(monkeypatch):
    # Varredura, item 8: regra lida pelo gunicorn.conf.py (proxy_config.py).
    from proxy_config import forwarded_allow_ips_from_env

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    assert forwarded_allow_ips_from_env() == "127.0.0.1"  # desenvolvimento, sem a variável

    monkeypatch.delenv("ENVIRONMENT")
    with pytest.raises(SystemExit):  # produção sem a variável: não sobe
        forwarded_allow_ips_from_env()

    for value, expected in (("127.0.0.1", "127.0.0.1"), ("*", "*"), (" 10.0.0.5 , 10.0.0.6 ", "10.0.0.5,10.0.0.6")):
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)
        assert forwarded_allow_ips_from_env() == expected
    for invalid in ("*,10.0.0.5", "10.0.0.0/8", "proxy.interno", "none"):
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", invalid)
        with pytest.raises(SystemExit):
            forwarded_allow_ips_from_env()


# ---------------------------------------------------------------------------
# Varredura de UI/UX: defeitos corrigidos
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_dashboard_does_not_count_cancelled_sessions(client):
    # Defeito 2: "Consultas no mês" e "Sessões futuras" somavam sessões canceladas.
    await _register(client, "painel.canceladas@example.com")
    future = datetime.now(timezone.utc) + timedelta(days=2)
    for status, hour in (("agendada", 9), ("agendada", 11), ("cancelada", 14)):
        start = future.replace(hour=hour, minute=0, second=0, microsecond=0)
        response = await client.post("/api/sessions", json={
            "title": "Sessão", "start": start.isoformat(), "end": (start + timedelta(minutes=50)).isoformat(),
            "status": status})
        assert response.status_code == 200

    stats = (await client.get("/api/dashboard/stats")).json()
    assert stats["upcoming_sessions"] == 2
    if future.month == datetime.now(timezone.utc).month:  # dois dias à frente ainda no mesmo mês
        assert stats["sessions_month"] == 2


@pytest.mark.anyio
async def test_unlock_message_uses_real_plural(client):
    # Defeito 9: "Restam 1 tentativa(s)" -> "Resta 1 tentativa" / "Restam 4 tentativas".
    await _register(client, "plural@example.com")
    await client.post("/api/auth/lock", json={"reason": "manual"})
    messages = [(await client.post("/api/auth/unlock", json={"password": f"errada-{i}-x"})).json()["detail"]["msg"]
                for i in range(4)]
    assert "Restam 4 tentativas antes" in messages[0]
    assert "Resta 1 tentativa antes" in messages[3]
    assert not any("(s)" in m for m in messages)


def test_count_label():
    assert server.count_label(1, "sessão encerrada", "sessões encerradas") == "1 sessão encerrada"
    assert server.count_label(0, "sessão encerrada", "sessões encerradas") == "0 sessões encerradas"
    assert server.count_label(3, "registro", "registros") == "3 registros"


@pytest.mark.anyio
async def test_patient_form_requires_every_field(client):
    # Produto: nenhum campo do cadastro pode ficar em branco (nem só com espaços).
    await _register(client, "obrigatorios@example.com")
    only_name = await client.post("/api/patients", json={"full_name": "Só o Nome"})
    assert only_name.status_code == 422
    message = only_name.json()["detail"][0]["msg"]
    for label in ("CPF", "RG", "Data de nascimento", "Escolaridade", "Estado civil", "Profissão", "Telefone",
                  "E-mail", "Endereço", "Contato de emergência", "Notas iniciais"):
        assert label in message, label

    blanks = await client.post("/api/patients", json=patient_data(rg="   ", initial_notes="  "))
    assert blanks.status_code == 422 and "RG, Notas iniciais" in blanks.json()["detail"][0]["msg"]

    # A idade não é digitada: sai da data de nascimento (e ignora um valor enviado).
    complete = await client.post("/api/patients", json=patient_data(birth_date="1990-01-02", age="99"))
    assert complete.status_code == 200
    assert complete.json()["age"] == str(server.age_from_birth_date(date(1990, 1, 2)))

    # A edição segue a mesma regra.
    pid = complete.json()["id"]
    assert (await client.put(f"/api/patients/{pid}", json=patient_data(address=""))).status_code == 422


def test_age_from_birth_date():
    assert server.age_from_birth_date(date(1990, 5, 14), today=date(2026, 5, 13)) == 35  # véspera do aniversário
    assert server.age_from_birth_date(date(1990, 5, 14), today=date(2026, 5, 14)) == 36
    assert server.age_from_birth_date(None) is None


@pytest.mark.anyio
async def test_logins_and_logouts_are_audited(client, monkeypatch):
    # Segurança alta: logins não entravam na trilha de auditoria; o profissional
    # não tinha como perceber um acesso indevido.
    chrome = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
    await _register(client, "auditoria.login@example.com")
    credentials = {"email": "auditoria.login@example.com", "password": "Senha-segura-123"}
    assert (await client.post("/api/auth/login", json={**credentials, "password": "errada-123"},
                              headers={"User-Agent": chrome})).status_code == 401
    assert (await client.post("/api/auth/login", json=credentials, headers={"User-Agent": chrome})).status_code == 200
    # E-mail sem conta: mesma resposta, nada para mostrar (não revela quem tem conta).
    assert (await client.post("/api/auth/login", json={**credentials, "email": "ninguem@example.com"})).status_code == 401

    logs = (await client.get("/api/audit")).json()["items"]
    actions = [(log["action"], log["detail"]) for log in logs]
    # Análise de UI (F3): termos em português ("Entrada", não "Login").
    assert ("entrar", "Entrada com senha (Chrome no Windows)") in actions
    assert ("entrar_falha", "Entrada recusada: senha incorreta (Chrome no Windows)") in actions
    assert all(log["ip_address"] for log in logs if log["action"].startswith("entrar"))

    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json=credentials)
    assert any(log["action"] == "sair" for log in (await client.get("/api/audit")).json()["items"])

    # Login com Google: cadastro e acesso registrados.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    async with _second_client() as other:
        with _google("novo.auditado@example.com", "sub-auditado"):
            await other.post("/api/auth/google", json={"credential": "t"}, headers={"User-Agent": chrome})
        await other.post("/api/auth/accept-terms", json={"version": legal.PRIVACY_POLICY_VERSION})
        google_logs = [(log["action"], log["detail"]) for log in (await other.get("/api/audit")).json()["items"]]
    assert ("criar", "Cadastro com Google") in google_logs
    assert ("entrar", "Entrada com Google (Chrome no Windows)") in google_logs


def test_describe_user_agent():
    edge = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/140.0 Safari/537.36 Edg/140.0"
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"
    android = "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36"
    assert server.describe_user_agent(edge) == "Edge no Windows"
    assert server.describe_user_agent(iphone) == "Safari no iPhone"
    assert server.describe_user_agent(android) == "Chrome no Android"
    assert server.describe_user_agent("") == "dispositivo não identificado"
    assert server.describe_user_agent("python-httpx/0.28") == "dispositivo não identificado"
    assert server.describe_user_agent("Mozilla/5.0 (X11; Linux x86_64) Konqueror/5") == "navegador não identificado no Linux"


# ---------------------------------------------------------------------------
# UX de maior impacto: agenda -> evolução, histórico de versões, última consulta
# ---------------------------------------------------------------------------

async def _session_for(client, patient_id, start, status="agendada"):
    begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
    response = await client.post("/api/sessions", json={
        "title": "Sessão", "patient_id": patient_id, "start": start,
        "end": (begin + timedelta(minutes=50)).isoformat(), "status": status, "allow_conflict": True})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.anyio
async def test_evolution_registered_from_agenda_session(client):
    patient_id = await _create_patient(client, "agenda.evolucao@example.com")
    session = await _session_for(client, patient_id, "2026-09-20T13:00:00Z")
    assert session["record_id"] == ""

    record = await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": "2026-09-20T13:00:00Z", "content": "Evolução da sessão", "session_id": session["id"]})
    assert record.status_code == 200 and record.json()["session_id"] == session["id"]

    listed = (await client.get("/api/sessions", params={"start": "2026-09-01T00:00:00Z"})).json()
    linked = next(s for s in listed if s["id"] == session["id"])
    assert linked["record_id"] == record.json()["id"]
    assert linked["status"] == "realizada"  # registrar a evolução confirma que a sessão aconteceu

    # Terminologia (UX): a trilha diz "Evolução registrada" e de onde ela veio.
    details = [log["detail"] for log in (await client.get("/api/audit")).json()["items"]]
    assert "Evolução registrada · sessão de 2026-09-20T13:00:00+00:00 (sessão da agenda)" in details

    # No máximo uma evolução por sessão: o 409 aponta a existente.
    again = await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": "2026-09-20T13:00:00Z", "content": "Duplicada", "session_id": session["id"]})
    assert again.status_code == 409
    assert again.json()["detail"]["record_id"] == record.json()["id"]

    # Sessão de outro paciente ou cancelada: recusada.
    other_patient = (await client.post("/api/patients", json=patient_data("Outra Pessoa"))).json()["id"]
    other_session = await _session_for(client, other_patient, "2026-09-21T13:00:00Z")
    cancelled = await _session_for(client, patient_id, "2026-09-22T13:00:00Z", status="cancelada")
    for sid in (other_session["id"], cancelled["id"]):
        refused = await client.post(f"/api/patients/{patient_id}/records", json={
            "session_datetime": "2026-09-22T13:00:00Z", "content": "x", "session_id": sid})
        assert refused.status_code == 422


@pytest.mark.anyio
async def test_last_consultation_only_moves_forward(client):
    patient_id = await _create_patient(client, "ultima.consulta@example.com")

    async def last():
        items = (await client.get("/api/patients")).json()["items"]
        return next(p for p in items if p["id"] == patient_id)["last_consultation_date"][:10]

    await client.post(f"/api/patients/{patient_id}/records", json={"session_datetime": "2026-09-24T13:00:00Z", "content": "a"})
    # Evolução de uma sessão ANTIGA registrada depois: a última consulta não volta no tempo.
    await client.post(f"/api/patients/{patient_id}/records", json={"session_datetime": "2026-09-10T13:00:00Z", "content": "b"})
    assert await last() == "2026-09-24"
    # Sessão realizada mais recente (e já ocorrida) também conta.
    await _session_for(client, patient_id, "2026-09-28T13:00:00Z", status="realizada")
    assert await last() == "2026-09-28"


@pytest.mark.anyio
async def test_record_version_history_is_visible(client):
    patient_id = await _create_patient(client, "historico@example.com")
    rid = (await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": "2026-09-15T10:00:00Z", "content": "Texto original"})).json()["id"]
    for version, text in ((1, "Primeira correção"), (2, "Segunda correção")):
        await client.put(f"/api/records/{rid}", json={
            "session_datetime": "2026-09-15T10:00:00Z", "content": text, "expected_version": version})

    history = (await client.get(f"/api/records/{rid}/versions")).json()
    assert history["current"]["version"] == 3 and history["current"]["content"] == "Segunda correção"
    assert [v["version"] for v in history["versions"]] == [2, 1]
    assert [v["content"] for v in history["versions"]] == ["Primeira correção", "Texto original"]
    assert all(v["replaced_at"] for v in history["versions"])
    logs = (await client.get("/api/audit")).json()["items"]
    assert any(log["action"] == "visualizar" and "Histórico de versões" in log["detail"] for log in logs)
    assert (await client.get("/api/records/rec_inexistente/versions")).status_code == 404


# ---------------------------------------------------------------------------
# Segurança média + backlog: e-mail, cadastro confirmado, recuperação de senha,
# MFA, sessões com aparelho, encerramento de conta, webhook e TLS
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_signup_requires_the_email_link(client):
    email = "novo.cadastro@example.com"
    response = await client.post("/api/auth/register", json={"name": "Ana Nova", "email": email, "terms_accepted": True})
    assert response.json() == {"status": "check_email", "email": email}
    # Nada de conta nem sessão antes de abrir o link.
    assert (await client.get("/api/auth/me")).status_code == 401
    mail = mailer.outbox[-1]
    assert mail.to == email and "/criar-senha#token=" in mail.text  # token no fragmento (#), fora dos logs
    token = link_token(email)

    check = await client.post("/api/auth/email-link/check", json={"token": token, "purpose": "signup"})
    assert check.json() == {"email": email, "name": "Ana Nova"}
    confirmed = await client.post("/api/auth/register/confirm", json={"token": token, "password": "Senha-segura-123"})
    assert confirmed.status_code == 200
    user = confirmed.json()["user"]
    assert user["email_verified"] is True and user["terms_accepted"] is True
    # O link vale uma vez só.
    again = await client.post("/api/auth/register/confirm", json={"token": token, "password": "Senha-segura-123"})
    assert again.status_code == 400 and again.json()["detail"]["code"] == "invalid_link"


@pytest.mark.anyio
async def test_signup_link_expires_and_new_link_replaces_old(client):
    email = "link.vencido@example.com"
    await client.post("/api/auth/register", json={"name": "A", "email": email, "terms_accepted": True})
    first = link_token(email)
    await client.post("/api/auth/register", json={"name": "A", "email": email, "terms_accepted": True})
    second = link_token(email)
    assert first != second
    # Pedir um link novo invalida o anterior.
    assert (await client.post("/api/auth/register/confirm", json={"token": first, "password": "Senha-segura-123"})).status_code == 400
    async with client.session_factory() as db:
        await db.execute(update(EmailToken).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
    assert (await client.post("/api/auth/register/confirm", json={"token": second, "password": "Senha-segura-123"})).status_code == 400


@pytest.mark.anyio
async def test_signup_does_not_reveal_existing_accounts(client):
    await register_account(client, "existe@example.com")
    async with _second_client() as other:
        known = await other.post("/api/auth/register", json={"name": "X", "email": "existe@example.com", "terms_accepted": True})
        unknown = await other.post("/api/auth/register", json={"name": "X", "email": "nao.existe@example.com", "terms_accepted": True})
    # Mesma resposta (a diferença só aparece no e-mail, que só o dono lê).
    assert known.status_code == unknown.status_code == 200
    assert known.json()["status"] == unknown.json()["status"] == "check_email"
    assert "já tem uma conta" in [m for m in mailer.outbox if m.to == "existe@example.com"][-1].text


@pytest.mark.anyio
async def test_forgot_and_reset_password(client):
    email = "esqueci@example.com"
    await register_account(client, email, password="Senha-antiga-1")
    async with _second_client() as other_device:
        await other_device.post("/api/auth/login", json={"email": email, "password": "Senha-antiga-1"})
        mailer.outbox.clear()
        assert (await client.post("/api/auth/password/forgot", json={"email": email})).json() == {"status": "check_email"}
        # E-mail sem conta: mesma resposta e nenhum e-mail enviado.
        assert (await client.post("/api/auth/password/forgot", json={"email": "ninguem@example.com"})).json() == {"status": "check_email"}
        assert [m.to for m in mailer.outbox] == [email]
        assert "/redefinir-senha#token=" in mailer.outbox[0].text
        token = link_token(email)

        reset = await client.post("/api/auth/password/reset", json={"token": token, "new_password": "Senha-nova-2"})
        assert reset.json() == {"ok": True}
        # Todas as sessões morrem (inclusive a de quem tinha a senha antiga).
        assert (await other_device.get("/api/auth/me")).status_code == 401
        assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.post("/api/auth/login", json={"email": email, "password": "Senha-antiga-1"})).status_code == 401
    assert (await client.post("/api/auth/login", json={"email": email, "password": "Senha-nova-2"})).status_code == 200
    # Link de uso único; aviso por e-mail; pedido na trilha.
    assert (await client.post("/api/auth/password/reset", json={"token": token, "new_password": "Outra-senha-3"})).status_code == 400
    assert any("Senha alterada" in m.subject for m in mailer.outbox)
    actions = {log["action"] for log in (await client.get("/api/audit")).json()["items"]}
    assert {"solicitar_senha", "alterar_senha"} <= actions


def test_totp_matches_rfc6238_vectors():
    # Vetores oficiais da RFC 6238 (SHA-1, segredo "12345678901234567890"), 6 últimos dígitos.
    secret = base64.b32encode(b"12345678901234567890").decode()
    for unix_time, expected in ((59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")):
        assert totp.code_at(secret, unix_time // 30) == expected
    step = totp.current_step()
    assert totp.verify(secret, totp.code_at(secret, step)) == step
    assert totp.verify(secret, totp.code_at(secret, step), last_step=step) is None  # código já usado
    assert totp.verify(secret, "12345") is None and totp.verify(secret, "abcdef") is None
    uri = totp.provisioning_uri(secret, "ana@example.com")
    assert uri.startswith("otpauth://totp/") and f"secret={secret}" in uri
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10 and all(len(c) == 19 for c in codes)


async def _enable_mfa(client):
    setup = (await client.post("/api/auth/mfa/setup")).json()
    secret = setup["secret"]
    enabled = await client.post("/api/auth/mfa/enable", json={"code": totp.code_at(secret, totp.current_step())})
    assert enabled.status_code == 200
    return secret, enabled.json()["recovery_codes"]


@pytest.mark.anyio
async def test_mfa_login_requires_the_app_code(client):
    email = "mfa@example.com"
    await register_account(client, email)
    secret, recovery = await _enable_mfa(client)
    assert (await client.get("/api/auth/me")).json()["mfa_enabled"] is True

    async with _second_client() as browser:
        step1 = await browser.post("/api/auth/login", json={"email": email, "password": "Senha-segura-123"})
        body = step1.json()
        assert body["mfa_required"] is True and "set-cookie" not in step1.headers
        assert (await browser.get("/api/auth/me")).status_code == 401  # ainda sem sessão
        # O token do passo 2 não serve como sessão.
        browser.cookies.set(server.auth_cookie_name(), body["mfa_token"])
        assert (await browser.get("/api/auth/me")).status_code == 401
        browser.cookies.clear()

        wrong = await browser.post("/api/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": "000000"})
        assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "mfa_invalid"
        # O código usado para ATIVAR não vale de novo (antirreplay): usa o próximo intervalo.
        code = totp.code_at(secret, totp.current_step() + 1)
        ok = await browser.post("/api/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": code})
        assert ok.status_code == 200 and (await browser.get("/api/auth/me")).status_code == 200
        replay = await browser.post("/api/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": code})
        assert replay.status_code == 401

        # Código de recuperação: vale uma vez.
        token = (await browser.post("/api/auth/login", json={"email": email, "password": "Senha-segura-123"})).json()["mfa_token"]
        assert (await browser.post("/api/auth/login/mfa", json={"mfa_token": token, "code": recovery[0].upper()})).status_code == 200
        assert (await browser.post("/api/auth/login/mfa", json={"mfa_token": token, "code": recovery[0]})).status_code == 401
    assert (await client.get("/api/auth/me")).json()["mfa_recovery_codes_left"] == 9
    assert any("Código de recuperação usado" in m.subject for m in mailer.outbox)


@pytest.mark.anyio
async def test_mfa_disable_needs_password_and_code(client):
    await register_account(client, "mfa.off@example.com")
    secret, _ = await _enable_mfa(client)
    next_code = lambda offset: totp.code_at(secret, totp.current_step() + offset)
    assert (await client.post("/api/auth/mfa/disable", json={"password": "errada-123", "code": next_code(1)})).status_code == 403
    assert (await client.post("/api/auth/mfa/disable", json={"password": "Senha-segura-123", "code": "000000"})).status_code == 403
    off = await client.post("/api/auth/mfa/disable", json={"password": "Senha-segura-123", "code": next_code(1)})
    assert off.status_code == 200 and off.json()["user"]["mfa_enabled"] is False
    logs = {log["action"] for log in (await client.get("/api/audit")).json()["items"]}
    assert {"mfa_ativar", "mfa_desativar"} <= logs


@pytest.mark.anyio
async def test_mfa_code_attempts_are_limited(client):
    email = "mfa.forca@example.com"
    await register_account(client, email)
    await _enable_mfa(client)
    token = (await client.post("/api/auth/login", json={"email": email, "password": "Senha-segura-123"})).json()["mfa_token"]
    statuses = [(await client.post("/api/auth/login/mfa", json={"mfa_token": token, "code": "000000"})).status_code
                for _ in range(6)]
    assert statuses == [401] * 5 + [429]


@pytest.mark.anyio
async def test_sessions_list_device_and_end_one(client):
    email = "aparelho@example.com"
    await register_account(client, email)
    async with _second_client() as phone:
        phone.headers["user-agent"] = "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36"
        await phone.post("/api/auth/login", json={"email": email, "password": "Senha-segura-123"})
        sessions = (await client.get("/api/auth/sessions")).json()["sessions"]
        other = next(s for s in sessions if not s["current"])
        assert other["device"] == "Chrome no Android" and other["ip_address"]
        current = next(s for s in sessions if s["current"])
        assert (await client.post(f"/api/auth/sessions/{current['id']}/end", json={"confirm": True})).status_code == 400
        assert (await client.post(f"/api/auth/sessions/{other['id']}/end", json={"confirm": True})).status_code == 200
        assert (await phone.get("/api/auth/me")).status_code == 401
        assert (await client.get("/api/auth/me")).status_code == 200
        assert (await client.post(f"/api/auth/sessions/{other['id']}/end", json={"confirm": True})).status_code == 404


@pytest.mark.anyio
async def test_account_export_zip(client):
    import zipfile
    patient_id = await _create_patient(client, "exporta.tudo@example.com")
    await client.post(f"/api/patients/{patient_id}/records", json={"session_datetime": "2026-09-10T13:00:00Z", "content": "Evolução"})
    response = await client.get("/api/account/export")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert "psigestao_completo.json" in names and "LEIA-ME.txt" in names
    assert any(n.startswith("pacientes/Paciente_Cripto_") and n.endswith(".pdf") for n in names)
    data = json.loads(archive.read("psigestao_completo.json"))
    assert data["pacientes"][0]["prontuarios"][0]["content"] == "Evolução"


@pytest.mark.anyio
async def test_close_account_keeps_only_what_the_law_requires(client):
    email = "encerrar@example.com"
    await register_account(client, email)
    kept = (await client.post("/api/patients", json=patient_data("Com Prontuário"))).json()["id"]
    gone = (await client.post("/api/patients", json=patient_data("Sem Prontuário"))).json()["id"]
    await client.post(f"/api/patients/{kept}/records", json={"session_datetime": "2026-09-10T13:00:00Z", "content": "x"})
    await _session_for(client, kept, "2026-10-20T13:00:00Z")

    wrong = await client.post("/api/account/close", json={"confirmation": "sim", "password": "Senha-segura-123"})
    assert wrong.status_code == 400
    bad_password = await client.post("/api/account/close", json={"confirmation": "ENCERRAR", "password": "errada-123"})
    assert bad_password.status_code == 403
    closed = await client.post("/api/account/close", json={"confirmation": "encerrar", "password": "Senha-segura-123"})
    assert closed.status_code == 200
    summary = closed.json()
    assert summary["deleted_patients"] == 1 and summary["retained_patients"] == 1 and summary["retained_until"]

    # Ninguém entra mais; o e-mail fica livre; aviso por e-mail.
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.post("/api/auth/login", json={"email": email, "password": "Senha-segura-123"})).status_code == 401
    assert any(m.to == email and "Conta encerrada" in m.subject for m in mailer.outbox)
    async with client.session_factory() as db:
        user = (await db.execute(select(User).where(User.closed_at.is_not(None)))).scalar_one()
        assert user.email.endswith("@contas-encerradas.invalid") and user.password_hash is None
        assert await db.get(Patient, gone) is None
        assert (await db.get(Patient, kept)).archived_at is not None  # sob guarda, fora da lista
        assert (await db.execute(select(Session).where(Session.owner_id == user.user_id))).first() is None
        logs = (await db.execute(select(AuditLog).where(AuditLog.owner_id == user.user_id))).scalars().all()
        assert any(log.action == "encerrar" and log.entity_type == "usuario" for log in logs)

        # Expurgo: antes do fim da guarda, nada sai; depois, sai tudo (inclusive a trilha).
        assert await server.purge_closed_accounts(db) == {"purged_patients": 0, "purged_accounts": 0}
        later = datetime.now(timezone.utc) + timedelta(days=365 * 6)
        assert await server.purge_closed_accounts(db, now=later) == {"purged_patients": 1, "purged_accounts": 1}
        assert await db.get(User, user.user_id) is None
        assert (await db.execute(select(AuditLog).where(AuditLog.owner_id == user.user_id))).first() is None
        # O gatilho "somente inclusão" continua valendo para todo o resto.
        db.add(AuditLog(owner_id="x", user_email="x@example.com", action="criar", entity_type="t", entity_id="1"))
        await db.commit()
        with pytest.raises(Exception):
            await db.execute(text("DELETE FROM audit_logs"))
            await db.commit()
        await db.rollback()

    # O mesmo e-mail pode criar uma conta nova.
    assert (await register_account(client, email)).status_code == 200


@pytest.mark.anyio
async def test_webhook_limit_is_per_token_and_tokens_expire(client, monkeypatch):
    monkeypatch.setattr(server, "WEBHOOK_LIMIT_PER_TOKEN", 2)
    tokens = []
    for email in ("forms.a@example.com", "forms.b@example.com"):
        async with _second_client() as browser:
            await register_account(browser, email)
            tokens.append((await browser.post("/api/integrations/google-forms/token")).json())
    assert tokens[0]["expires_at"] and tokens[0]["expired"] is False
    payload = {"patient_data": {"full_name": "Via Forms"}}

    send = lambda token: client.post("/api/webhook/google-forms", json=payload, headers={"X-Webhook-Token": token})
    first = [(await send(tokens[0]["token"])).status_code for _ in range(3)]
    assert first == [200, 200, 429]
    # Mesmo IP (como os servidores do Google), outro formulário: não é afetado.
    assert (await send(tokens[1]["token"])).status_code == 200

    async with client.session_factory() as db:
        await db.execute(update(User).where(User.email == "forms.b@example.com")
                         .values(webhook_token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
    expired = await send(tokens[1]["token"])
    assert expired.status_code == 401 and "vencido" in expired.json()["detail"]


@pytest.mark.anyio
async def test_webhook_token_expiry_reminder(client):
    await register_account(client, "lembrete@example.com")
    await client.post("/api/integrations/google-forms/token")
    async with client.session_factory() as db:
        await db.execute(update(User).where(User.email == "lembrete@example.com")
                         .values(webhook_token_expires_at=datetime.now(timezone.utc) + timedelta(days=10)))
        await db.commit()
        mailer.outbox.clear()
        assert await server.notify_expiring_webhook_tokens(db) == 1
        assert await server.notify_expiring_webhook_tokens(db) == 0  # um aviso só por token
    assert [m.to for m in mailer.outbox] == ["lembrete@example.com"]


@pytest.mark.anyio
async def test_audit_filters_and_csv_export(client):
    await register_account(client, "trilha@example.com")
    await client.post("/api/auth/logout")
    await client.post("/api/auth/login", json={"email": "trilha@example.com", "password": "Senha-segura-123"})
    logins = (await client.get("/api/audit", params={"action": "entrar"})).json()["items"]
    assert logins and all(log["action"] == "entrar" for log in logins)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert (await client.get("/api/audit", params={"start": future})).json()["items"] == []

    export = await client.get("/api/audit/export", params={"action": "entrar"})
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    content = export.content.decode("utf-8-sig")
    header, *rows = content.strip().splitlines()
    assert header.startswith("Data e hora") and ";Ação;" in header
    assert rows and all(";Entrada;" in row for row in rows)  # F3: rótulo em português
    assert server._csv_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"  # sem fórmula no Excel
    # A própria exportação fica na trilha.
    assert any(log["action"] == "exportar" and log["entity_type"] == "auditoria"
               for log in (await client.get("/api/audit")).json()["items"])


@pytest.mark.anyio
async def test_patient_list_sort_total_and_next_session(client):
    await register_account(client, "ordem@example.com")
    ids = {}
    for name in ("Érica Lima", "bruno Alves", "Ana Souza"):
        ids[name] = (await client.post("/api/patients", json=patient_data(name))).json()["id"]
    future = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    await _session_for(client, ids["Ana Souza"], future.isoformat())
    await _session_for(client, ids["Ana Souza"], (future - timedelta(days=10)).isoformat())  # passada: não conta

    by_name = (await client.get("/api/patients", params={"sort": "name", "limit": 2})).json()
    assert by_name["total"] == 3
    assert [p["full_name"] for p in by_name["items"]] == ["Ana Souza", "bruno Alves"]  # sem diferenciar acentos/maiúsculas
    rest = (await client.get("/api/patients", params={"sort": "name", "limit": 2, "cursor": by_name["next_cursor"]})).json()
    assert [p["full_name"] for p in rest["items"]] == ["Érica Lima"] and rest["next_cursor"] is None
    ana = by_name["items"][0]
    assert datetime.fromisoformat(ana["next_session"]) == future
    assert by_name["items"][1]["next_session"] == ""
    assert (await client.get("/api/patients", params={"sort": "outra"})).status_code == 422


@pytest.mark.anyio
async def test_records_filtered_by_period(client):
    patient_id = await _create_patient(client, "periodo@example.com")
    for day in (5, 15, 25):
        await client.post(f"/api/patients/{patient_id}/records",
                          json={"session_datetime": f"2026-09-{day:02d}T13:00:00Z", "content": f"dia {day}"})
    page = (await client.get(f"/api/patients/{patient_id}/records",
                             params={"start": "2026-09-10T00:00:00Z", "end": "2026-09-20T00:00:00Z"})).json()
    assert [r["content"] for r in page["items"]] == ["dia 15"] and page["total"] == 1


def test_mail_is_required_in_production(monkeypatch):
    for name in ("MAIL_BACKEND", "SMTP_HOST", "SMTP_FROM", "SMTP_SECURITY"):
        monkeypatch.delenv(name, raising=False)
    mailer.check_mail_config("development")  # desenvolvimento: sem SMTP, o e-mail vai para o log
    with pytest.raises(SystemExit):
        mailer.check_mail_config("production")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "PsiGestão <nao-responda@example.com>")
    mailer.check_mail_config("production")
    monkeypatch.setenv("SMTP_SECURITY", "none")  # sem criptografia: nunca em produção
    with pytest.raises(SystemExit):
        mailer.check_mail_config("production")


def test_email_links_carry_the_token_in_the_fragment(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.exemplo.com.br/")
    assert server.frontend_link("/redefinir-senha", "abc") == "https://app.exemplo.com.br/redefinir-senha#token=abc"
    assert server.frontend_link("/login") == "https://app.exemplo.com.br/login"


# ---------------------------------------------------------------------------
# Fluxo clínico: C4 (faltou), C2 (evoluções pendentes), P1 (séries), C3 (menores)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_show_status(client):
    patient_id = await _create_patient(client, "faltou@example.com")
    missed = await _session_for(client, patient_id, "2026-09-20T13:00:00Z", status="faltou")
    assert missed["status"] == "faltou"
    # Falta não ocupa o horário: marcar outra sessão no mesmo horário não dá conflito.
    same_slot = await client.post("/api/sessions", json={
        "title": "Encaixe", "patient_id": patient_id, "start": "2026-09-20T13:00:00Z",
        "end": "2026-09-20T13:50:00Z", "status": "agendada"})
    assert same_slot.status_code == 200
    # Falta não recebe evolução ligada (a ligação marcaria a sessão como realizada).
    linked = await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": "2026-09-20T13:00:00Z", "content": "x", "session_id": missed["id"]})
    assert linked.status_code == 422 and "falta" in linked.json()["detail"]
    # Sessão com evolução não vira falta.
    done = await _session_for(client, patient_id, "2026-09-21T13:00:00Z")
    await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": "2026-09-21T13:00:00Z", "content": "x", "session_id": done["id"]})
    payload = {"title": "Sessão", "patient_id": patient_id, "start": "2026-09-21T13:00:00Z", "status": "faltou"}
    assert (await client.put(f"/api/sessions/{done['id']}", json=payload)).status_code == 409


@pytest.mark.anyio
async def test_no_show_is_not_counted_as_consultation(client):
    patient_id = await _create_patient(client, "faltou.painel@example.com")
    future = datetime.now(timezone.utc) + timedelta(days=2)
    await _session_for(client, patient_id, future.isoformat(), status="agendada")
    await _session_for(client, patient_id, (future + timedelta(hours=2)).isoformat(), status="faltou")
    stats = (await client.get("/api/dashboard/stats")).json()
    assert stats["upcoming_sessions"] == 1


@pytest.mark.anyio
async def test_pending_evolutions(client):
    patient_id = await _create_patient(client, "pendentes@example.com")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    pending_old = await _session_for(client, patient_id, (now - timedelta(days=3)).isoformat())
    pending_done = await _session_for(client, patient_id, (now - timedelta(days=2)).isoformat(), status="realizada")
    with_record = await _session_for(client, patient_id, (now - timedelta(days=1)).isoformat())
    await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": (now - timedelta(days=1)).isoformat(), "content": "x", "session_id": with_record["id"]})
    await _session_for(client, patient_id, (now - timedelta(days=4)).isoformat(), status="faltou")      # falta: não pede
    await _session_for(client, patient_id, (now - timedelta(days=5)).isoformat(), status="cancelada")   # cancelada: não pede
    await _session_for(client, patient_id, (now + timedelta(days=1)).isoformat())                       # futura: ainda não
    await _session_for(client, patient_id, (now - timedelta(days=120)).isoformat())                     # fora da janela
    no_patient = await client.post("/api/sessions", json={
        "title": "Reunião", "start": (now - timedelta(days=1, hours=3)).isoformat(), "status": "agendada", "allow_conflict": True})
    assert no_patient.status_code == 200

    page = (await client.get("/api/sessions/pending-evolutions")).json()
    assert page["total"] == 2 and page["window_days"] == 90
    assert [item["id"] for item in page["items"]] == [pending_done["id"], pending_old["id"]]  # mais recente primeiro
    # `before`: o painel pede só os dias anteriores a hoje.
    before = (now - timedelta(days=2, hours=12)).isoformat()
    only_old = (await client.get("/api/sessions/pending-evolutions", params={"before": before})).json()["items"]
    assert [i["id"] for i in only_old] == [pending_old["id"]]
    # Registrar a evolução resolve a pendência.
    await client.post(f"/api/patients/{patient_id}/records", json={
        "session_datetime": pending_old["start"], "content": "x", "session_id": pending_old["id"]})
    assert (await client.get("/api/sessions/pending-evolutions")).json()["total"] == 1


@pytest.mark.anyio
async def test_recurring_sessions_create_and_conflicts(client):
    patient_id = await _create_patient(client, "serie@example.com")
    body = {"title": "Terapia", "patient_id": patient_id, "start": "2026-11-03T13:00:00Z",
            "end": "2026-11-03T13:50:00Z", "status": "agendada", "repeat": {"frequency": "weekly", "count": 4}}
    created = await client.post("/api/sessions", json=body)
    assert created.status_code == 200 and created.json()["series_count"] == 4
    assert created.json()["series_id"].startswith("ser_") and created.json()["recurrence"] == "weekly"
    window = {"start": "2026-11-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"}
    listed = (await client.get("/api/sessions", params=window)).json()
    assert [s["start"][:10] for s in listed] == ["2026-11-03", "2026-11-10", "2026-11-17", "2026-11-24"]
    assert all(s["end"][11:16] == "13:50" for s in listed)

    # Quinzenal que bate em 03/11 e 17/11 da série semanal: nada é criado.
    biweekly = {**body, "start": "2026-11-03T13:30:00Z", "end": "2026-11-03T14:20:00Z",
                "repeat": {"frequency": "biweekly", "count": 3}}
    conflict = await client.post("/api/sessions", json=biweekly)
    assert conflict.status_code == 409 and len(conflict.json()["detail"]["conflicts"]) == 2
    assert len((await client.get("/api/sessions", params=window)).json()) == 4
    # Com "salvar mesmo assim" a série entra inteira.
    forced = await client.post("/api/sessions", json={**biweekly, "allow_conflict": True})
    assert forced.json()["series_count"] == 3
    # Só sessão agendada se repete; e no máximo 52.
    assert (await client.post("/api/sessions", json={**body, "status": "realizada"})).status_code == 422
    assert (await client.post("/api/sessions", json={**body, "repeat": {"frequency": "weekly", "count": 53}})).status_code == 422


@pytest.mark.anyio
async def test_recurring_sessions_edit_and_delete_following(client):
    patient_id = await _create_patient(client, "serie.edicao@example.com")
    body = {"title": "Terapia", "patient_id": patient_id, "start": "2026-11-03T13:00:00Z",
            "end": "2026-11-03T13:50:00Z", "status": "agendada", "repeat": {"frequency": "weekly", "count": 5}}
    await client.post("/api/sessions", json=body)
    window = {"start": "2026-11-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"}
    series = (await client.get("/api/sessions", params=window)).json()
    # A 4ª virou falta: é histórico e não deve mudar com a série.
    fourth = series[3]
    await client.put(f"/api/sessions/{fourth['id']}", json={**body, "repeat": None, "start": fourth["start"],
                                                            "end": fourth["end"], "status": "faltou"})

    # Mover a 2ª e as próximas uma hora para frente, com 60 min de duração.
    second = series[1]
    moved = await client.put(f"/api/sessions/{second['id']}", json={
        **body, "repeat": None, "start": "2026-11-10T14:00:00Z", "end": "2026-11-10T15:00:00Z", "apply_to": "following"})
    assert moved.status_code == 200 and moved.json()["updated_following"] == 2  # 3ª e 5ª (a 4ª é falta)
    after = {s["id"]: s for s in (await client.get("/api/sessions", params=window)).json()}
    assert after[series[0]["id"]]["start"][11:16] == "13:00"                       # anterior: igual
    assert after[series[2]["id"]]["start"][11:16] == "14:00" and after[series[2]["id"]]["end"][11:16] == "15:00"
    assert after[series[3]["id"]]["start"][11:16] == "13:00"                       # falta: igual
    assert after[series[4]["id"]]["start"][11:16] == "14:00"

    # "Só esta" não mexe nas outras.
    only = await client.put(f"/api/sessions/{series[2]['id']}", json={
        **body, "repeat": None, "start": "2026-11-17T16:00:00Z", "end": "2026-11-17T17:00:00Z"})
    assert only.json()["updated_following"] == 0
    assert after[series[4]["id"]]["start"] == (await client.get("/api/sessions", params=window)).json()[4]["start"]

    # Remover a 3ª e as próximas: sai a 3ª e a 5ª; a 1ª, a 2ª e a falta ficam.
    deleted = await client.delete(f"/api/sessions/{series[2]['id']}", params={"scope": "following"})
    assert deleted.json() == {"ok": True, "deleted": 2}
    remaining = [s["id"] for s in (await client.get("/api/sessions", params=window)).json()]
    assert remaining == [series[0]["id"], series[1]["id"], series[3]["id"]]
    logs = (await client.get("/api/audit")).json()["items"]
    assert any("próxima sessão da série" in log["detail"] for log in logs)


@pytest.mark.anyio
async def test_minor_patient_requires_guardian(client):
    await register_account(client, "menor@example.com")
    birth = (date.today() - timedelta(days=365 * 10)).isoformat()
    minor = patient_data("Criança Teste", birth_date=birth, rg="", marital_status="", profession="", phone="", email="")
    missing = await client.post("/api/patients", json=minor)
    assert missing.status_code == 422
    message = missing.json()["detail"][0]["msg"]
    assert "Nome do responsável" in message and "Estado civil" not in message and "RG" not in message

    guardian = {"guardian_name": "Maria Souza", "guardian_cpf": "529.982.247-25",
                "guardian_relationship": "Mãe", "guardian_phone": "(11) 91234-5678"}
    bad_cpf = await client.post("/api/patients", json={**minor, **guardian, "guardian_cpf": "111.111.111-11"})
    assert bad_cpf.status_code == 422
    ok = await client.post("/api/patients", json={**minor, **guardian, "consent_terms": True})
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["is_minor"] is True and data["guardian_name"] == "Maria Souza" and data["guardian_relationship"] == "Mãe"
    assert data["consent_by_guardian"] is True and data["consent_terms_status"] == "Concordo (pelo responsável legal)"
    # Nome, CPF e telefone do responsável ficam criptografados no banco.
    async with client.session_factory() as db:
        row = await db.get(Patient, data["id"])
        assert "Maria" not in row.guardian_name and "529" not in row.guardian_cpf and row.guardian_relationship == "Mãe"
    # Adulto continua exigindo tudo (estado civil, profissão...).
    adult = await client.post("/api/patients", json=patient_data("Adulto", marital_status=""))
    assert adult.status_code == 422 and "Estado civil" in adult.json()["detail"][0]["msg"]
    exported = await client.get(f"/api/patients/{data['id']}/export", params={"format": "json"})
    assert exported.json()["paciente"]["guardian_name"] == "Maria Souza"
    pdf = await client.get(f"/api/patients/{data['id']}/export", params={"format": "pdf"})
    assert pdf.status_code == 200


def test_minor_age_rule():
    today = date(2026, 10, 5)
    assert server.is_minor(date(2008, 10, 6), today) is True    # faz 18 amanhã
    assert server.is_minor(date(2008, 10, 5), today) is False   # fez 18 hoje
    assert server.is_minor(None, today) is False


# ---------------------------------------------------------------------------
# Análise de UI: produtividade (P2, P5, P6, P7) e primeiro uso (O1, O2)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_search_by_phone_and_cpf_uses_blind_index(client):
    """P5: quando o paciente liga, o que se tem é o telefone (ou o CPF)."""
    await _register(client, "busca.telefone@example.com")
    ana = (await client.post("/api/patients", json=patient_data(
        "Ana Telefone", phone="(11) 91234-5678", cpf="529.982.247-25"))).json()["id"]
    await client.post("/api/patients", json=patient_data("Bruno Outro", phone="(21) 3333-4444", cpf="123.456.789-09"))
    birth = (date.today() - timedelta(days=365 * 10)).isoformat()
    child = (await client.post("/api/patients", json={**patient_data(
        "Criança Fone", birth_date=birth, rg="", marital_status="", profession="", phone="", email=""),
        "guardian_name": "Mãe Fone", "guardian_cpf": "111.444.777-35", "guardian_relationship": "Mãe",
        "guardian_phone": "(31) 98888-7777"})).json()["id"]

    async def search(q):
        response = await client.post("/api/patients/search", json={"q": q})
        assert response.status_code == 200
        return [p["id"] for p in response.json()["items"]]

    for q in ("(11) 91234-5678", "11912345678", "91234-5678", "912345678", "+55 11 91234 5678", "1234-5678"):
        assert await search(q) == [ana], q                      # com ou sem DDD, 9, +55
    assert await search("529.982.247-25") == [ana]               # CPF com pontuação
    assert await search("52998224725") == [ana]                  # CPF só números
    assert await search("98888-7777") == [child]                 # telefone do responsável
    assert await search("111.444.777-35") == [child]             # CPF do responsável
    assert await search("91234") == []                           # menos de 8 dígitos: não identifica
    assert await search("ana") == [ana]                          # busca por nome continua igual

    # Nenhum token guarda o número em claro.
    async with client.session_factory() as db:
        tokens = (await db.execute(select(PatientSearchToken.token))).scalars().all()
    assert not any("912345678" in t or "52998224725" in t for t in tokens)

    # Edição troca o número no índice; anonimização tira os contatos (e os do responsável).
    await client.put(f"/api/patients/{ana}", json=patient_data("Ana Telefone", phone="(11) 95555-0000",
                                                                 cpf="529.982.247-25"))
    assert await search("91234-5678") == [] and await search("95555-0000") == [ana]
    anonymized = (await client.post(f"/api/patients/{child}/anonymize")).json()
    assert anonymized["guardian_name"] == "" and anonymized["guardian_phone"] == "" and anonymized["guardian_cpf"] == ""
    assert await search("98888-7777") == []

    # Isolamento: outro profissional não acha o paciente pelo telefone.
    await _register(client, "outro.telefone@example.com")
    assert await search("95555-0000") == []


@pytest.mark.anyio
async def test_rebuild_search_index_adds_contacts_to_old_patients(client):
    """P5: pacientes indexados antes (só nome) ganham telefone e CPF no rebuild."""
    await _register(client, "indice.antigo@example.com")
    pid = (await client.post("/api/patients", json=patient_data("Paciente Antigo", phone="(11) 97777-1234"))).json()["id"]
    async with client.session_factory() as db:
        # Simula o índice da versão 1: sem tokens de contato e sem versão.
        await db.execute(PatientSearchToken.__table__.delete())
        await db.execute(update(Patient).values(search_index_version=None))
        await db.commit()
    assert (await client.post("/api/patients/search", json={"q": "97777-1234"})).json()["items"] == []

    assert await rebuild_search_index.rebuild(session_factory=client.session_factory) == 1
    found = (await client.post("/api/patients/search", json={"q": "97777-1234"})).json()["items"]
    assert [p["id"] for p in found] == [pid]
    assert (await client.post("/api/patients/search", json={"q": "antigo"})).json()["items"][0]["id"] == pid
    assert await rebuild_search_index.rebuild(session_factory=client.session_factory) == 0  # já atualizado


@pytest.mark.anyio
async def test_session_title_is_generated_when_blank(client):
    """P6: o título era obrigatório e quase sempre "Sessão"."""
    patient_id = await _create_patient(client, "titulo.auto@example.com")
    with_patient = await client.post("/api/sessions", json={"start": "2026-11-02T13:00:00Z", "patient_id": patient_id})
    assert with_patient.status_code == 200 and with_patient.json()["title"] == "Sessão"
    alone = await client.post("/api/sessions", json={"start": "2026-11-03T13:00:00Z", "title": ""})
    assert alone.json()["title"] == "Compromisso"
    typed = await client.post("/api/sessions", json={"start": "2026-11-04T13:00:00Z", "title": " Avaliação inicial "})
    assert typed.json()["title"] == "Avaliação inicial"


@pytest.mark.anyio
async def test_patient_sessions_for_the_record(client):
    """P2: a ficha mostra as próximas sessões e o resumo do que já passou."""
    patient_id = await _create_patient(client, "agenda.ficha@example.com")
    other = (await client.post("/api/patients", json=patient_data("Outro Paciente"))).json()["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)

    async def make(days, status="agendada", pid=patient_id):
        start = (now + timedelta(days=days)).isoformat()
        response = await client.post("/api/sessions", json={
            "patient_id": pid, "start": start, "status": status, "allow_conflict": True})
        assert response.status_code == 200, response.text
        return response.json()["id"]

    await make(-14, "realizada")
    await make(-7, "realizada")
    await make(-3, "faltou")
    first = await make(2)
    await make(4, "cancelada")
    third = await make(9)
    await make(3, pid=other)  # de outro paciente: não aparece

    data = (await client.get(f"/api/patients/{patient_id}/sessions")).json()
    assert data["upcoming"][0]["id"] == first and data["upcoming"][-1]["id"] == third
    assert len(data["upcoming"]) == 3            # inclui a cancelada (mostrada com o status)
    assert data["upcoming_total"] == 2           # mas ela não ocupa horário
    assert data["past"] == {"realizada": 2, "faltou": 1, "cancelada": 0, "agendada": 0}
    assert len((await client.get(f"/api/patients/{patient_id}/sessions", params={"limit": 1})).json()["upcoming"]) == 1

    await _register(client, "intruso.ficha@example.com")
    assert (await client.get(f"/api/patients/{patient_id}/sessions")).status_code == 404


@pytest.mark.anyio
async def test_agenda_hours_setting(client):
    """P7: horário de atendimento da grade da agenda."""
    await register_account(client, "horario@example.com")
    me = (await client.get("/api/auth/me")).json()
    assert (me["agenda_start_hour"], me["agenda_end_hour"]) == (7, 21)  # padrão

    saved = await client.put("/api/auth/agenda-settings", json={"start_hour": 8, "end_hour": 19})
    assert saved.status_code == 200 and (saved.json()["agenda_start_hour"], saved.json()["agenda_end_hour"]) == (8, 19)
    assert (await client.put("/api/auth/agenda-settings", json={"start_hour": 0, "end_hour": 24})).status_code == 200
    for bad in ({"start_hour": 10, "end_hour": 11}, {"start_hour": 9, "end_hour": 25}, {"start_hour": -1, "end_hour": 8}):
        assert (await client.put("/api/auth/agenda-settings", json=bad)).status_code == 422, bad
    logs = (await client.get("/api/audit")).json()["items"]
    assert any(log["detail"] == "Horário de atendimento: 08:00 às 19:00" for log in logs)


@pytest.mark.anyio
async def test_onboarding_first_steps(client):
    """O1: os passos da lista "Primeiros passos" e o "Ocultar"."""
    await register_account(client, "primeiros.passos@example.com")
    stats = (await client.get("/api/dashboard/stats")).json()
    assert stats["onboarding"] == {"has_patients": False, "has_sessions": False}
    me = (await client.get("/api/auth/me")).json()
    assert me["consent_term_reviewed"] is False and me["onboarding_dismissed"] is False

    # Revisar o termo sem mudar nada também conta (e fica na auditoria como revisão).
    term = (await client.get("/api/consent-term")).json()
    assert (await client.put("/api/consent-term", json={"body": term["body"]})).json()["is_default"] is True
    assert (await client.get("/api/auth/me")).json()["consent_term_reviewed"] is True
    logs = (await client.get("/api/audit")).json()["items"]
    assert any(log["detail"] == "Modelo do termo de consentimento (revisado, sem alteração)" for log in logs)

    pid = (await client.post("/api/patients", json=patient_data("Primeiro Paciente"))).json()["id"]
    await client.post("/api/sessions", json={"patient_id": pid, "start": "2026-11-05T13:00:00Z"})
    stats = (await client.get("/api/dashboard/stats")).json()
    assert stats["onboarding"] == {"has_patients": True, "has_sessions": True}

    assert (await client.put("/api/auth/onboarding", json={"dismissed": True})).json()["onboarding_dismissed"] is True
    assert (await client.put("/api/auth/onboarding", json={"dismissed": False})).json()["onboarding_dismissed"] is False


@pytest.mark.anyio
async def test_help_contact_is_public(client, monkeypatch):
    """O2: a página de Ajuda mostra o suporte e o encarregado, sem exigir login."""
    monkeypatch.setenv("SUPPORT_EMAIL", "suporte@example.com")
    monkeypatch.setenv("PRIVACY_DPO_NAME", "Encarregada Teste")
    monkeypatch.setenv("PRIVACY_DPO_EMAIL", "dpo@example.com")
    data = (await client.get("/api/legal/contact")).json()
    assert data == {"support_email": "suporte@example.com", "dpo_name": "Encarregada Teste", "dpo_email": "dpo@example.com"}
    # Não configurado: vazio (a tela esconde a linha), nunca o marcador "[A PREENCHER]".
    monkeypatch.delenv("SUPPORT_EMAIL")
    monkeypatch.delenv("PRIVACY_DPO_EMAIL")
    data = (await client.get("/api/legal/contact")).json()
    assert data["support_email"] == "" and data["dpo_email"] == ""
