import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
os.environ.setdefault("JWT_SECRET", "route-test-secret")
os.environ.setdefault("FERNET_KEY", "route-test-fernet-secret")

from database import Base, get_db
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as test_client:
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
    token = response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

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
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

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
async def test_google_session_route_uses_external_session_data(client):
    external_response = Mock(
        status_code=200,
        json=lambda: {
            "email": "google.test@example.com",
            "name": "Google Test",
            "picture": "https://example.com/avatar.png",
            "session_token": "google-session-token",
        },
    )
    with patch("server.httpx.AsyncClient.get", new=AsyncMock(return_value=external_response)):
        response = await client.post("/api/auth/session", headers={"X-Session-ID": "oauth-id"})

    assert response.status_code == 200
    assert response.json()["user"]["auth_provider"] == "google"
    assert "session_token" in response.cookies
    assert (await client.get("/api/auth/me")).status_code == 200