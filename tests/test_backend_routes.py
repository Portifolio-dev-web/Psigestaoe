import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
os.environ.setdefault("JWT_SECRET", "route-test-secret")
# Precisa ser uma chave Fernet válida: o servidor agora valida a chave na inicialização.
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ.setdefault("FERNET_KEY", TEST_FERNET_KEY)

from database import Base, get_db
import rate_limit
import rotate_encryption_key
import server
from crypto import legacy_key_from_secret
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
    # Item 9: o limitador é global ao processo; zera entre testes para um não afetar o outro.
    rate_limit.limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as test_client:
        test_client.session_factory = session_factory
        yield test_client

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.anyio
async def test_all_backend_routes(client):
    email = "route.test@example.com"
    password = "Senha-segura-123"
    patient_payload = {
        "full_name": "Paciente de Teste",
        "cpf": "12345678900",
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
    webhook_token = me.json()["webhook_token"]

    assert (await client.get("/api/patients", headers=headers)).json() == []
    patient = await client.post("/api/patients", headers=headers, json=patient_payload)
    assert patient.status_code == 200
    patient_id = patient.json()["id"]
    assert patient.json()["cpf"] == patient_payload["cpf"]
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
    assert records.status_code == 200 and records.json() == []
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
    assert next(p for p in forms_patient.json() if p["full_name"] == "Paciente via Forms")["birth_date"] == "1992-01-14"

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
    assert any(item["entity_type"] == "prontuario" for item in audit.json())

    anonymized = await client.post(f"/api/patients/{patient_id}/anonymize", headers=headers)
    assert anonymized.status_code == 200
    assert anonymized.json()["anonymized"] is True
    assert anonymized.json()["full_name"] == "Paciente Anonimizado"

    assert (await client.delete(f"/api/sessions/{session_id}", headers=headers)).json() == {"ok": True}
    assert (await client.delete("/api/sessions/inexistente", headers=headers)).status_code == 404
    assert (await client.delete(f"/api/patients/{patient_id}", headers=headers)).json() == {"ok": True}
    assert (await client.delete(f"/api/patients/{patient_id}", headers=headers)).status_code == 404

    logout = await client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}


@pytest.mark.anyio
async def test_google_auth_route_creates_user_and_session(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    id_info = {
        "email": "Google.Test@example.com",
        "email_verified": True,
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
        "full_name": "Paciente Cripto", "cpf": "12345678900", "address": "Rua A, 1",
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
    assert response.json()["cpf"] == "12345678900"
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
async def test_consent_term_uses_owner_name_and_crp(client):
    # Item 6: o termo não pode mais citar uma psicóloga fixa no código.
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
