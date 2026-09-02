from dotenv import load_dotenv
from pathlib import Path
import os

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import logging
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
import uuid
import base64
import json
import io
import secrets as pysecrets
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ['JWT_SECRET']
JWT_ALGORITHM = "HS256"

app = FastAPI(title="PsiGestão API")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("psigestao")

# ---------------------------------------------------------------------------
# Field-level encryption (AES-256-GCM)
# ---------------------------------------------------------------------------
def _enc_key() -> bytes:
    return base64.b64decode(os.environ['ENCRYPTION_KEY'])

def encrypt_field(plaintext: Optional[str]) -> Optional[str]:
    if plaintext is None or plaintext == "":
        return plaintext
    aes = AESGCM(_enc_key())
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("utf-8")

def decrypt_field(token: Optional[str]) -> Optional[str]:
    if token is None or token == "":
        return token
    try:
        raw = base64.b64decode(token)
        aes = AESGCM(_enc_key())
        return aes.decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception:
        return token  # value stored before encryption / already plaintext

# ---------------------------------------------------------------------------
# Password + JWT helpers
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email,
               "exp": datetime.now(timezone.utc) + timedelta(days=7), "type": "access"}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def set_auth_cookie(response: Response, token: str):
    response.set_cookie(key="access_token", value=token, httponly=True, secure=True,
                        samesite="none", max_age=604800, path="/")

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def iso(dt: datetime) -> str:
    return dt.isoformat()

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class RegisterInput(BaseModel):
    name: str
    email: EmailStr
    password: str
    terms_accepted: bool = False

class LoginInput(BaseModel):
    email: EmailStr
    password: str

class PatientInput(BaseModel):
    full_name: str
    cpf: Optional[str] = ""
    rg: Optional[str] = ""                  # Novo
    birth_date: Optional[str] = ""
    age: Optional[str] = ""                 # Novo (Nota: Em sistemas reais, calcula-se a idade pela data de nascimento, mas manteremos para espelhar o form)
    education: Optional[str] = ""           # Novo (Escolaridade)
    profession: Optional[str] = ""          # Novo (Profissão)
    phone: Optional[str] = ""
    email: Optional[str] = ""
    address: Optional[str] = ""             # Novo (Endereço Completo)
    emergency_contact: Optional[str] = ""
    initial_notes: Optional[str] = ""
    consent_terms: bool = False             # Novo (Termo de Consentimento)

class RecordInput(BaseModel):
    session_datetime: str
    content: str
    diagnosis: Optional[str] = ""

class SessionInput(BaseModel):
    patient_id: Optional[str] = ""
    patient_name: Optional[str] = ""
    title: str
    start: str
    end: Optional[str] = ""
    status: str = "agendada"
    notes: Optional[str] = ""

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def get_current_user(request: Request) -> dict:
    # 1) Emergent Google session token
    session_token = request.cookies.get("session_token")
    if not session_token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            session_token = auth[7:]
    if session_token:
        sess = await db.user_sessions.find_one({"session_token": session_token}, {"_id": 0})
        if sess:
            expires_at = sess["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at >= now_utc():
                user = await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0, "password_hash": 0})
                if user:
                    return user

    # 2) JWT access token (email/password)
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if token:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0})
            if user:
                return user
        except jwt.PyJWTError:
            pass

    raise HTTPException(status_code=401, detail="Não autenticado")

# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
async def log_audit(owner_id: str, user_email: str, action: str, entity_type: str,
                    entity_id: str, detail: str = ""):
    await db.audit_logs.insert_one({
        "log_id": f"log_{uuid.uuid4().hex[:12]}",
        "owner_id": owner_id,
        "user_email": user_email,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "detail": detail,
        "timestamp": iso(now_utc()),
    })

# Atualize a função public_user para incluir o token
def public_user(u: dict) -> dict:
    return {
        "user_id": u["user_id"], 
        "email": u["email"], 
        "name": u.get("name", ""),
        "picture": u.get("picture", ""), 
        "auth_provider": u.get("auth_provider", "email"),
        "terms_accepted": u.get("terms_accepted", False),
        "webhook_token": u.get("webhook_token", "") # NOVO
    }

# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@api_router.post("/auth/register")
async def register(data: RegisterInput, response: Response):
    if not data.terms_accepted:
        raise HTTPException(status_code=400, detail="É necessário aceitar os Termos de Privacidade (LGPD).")
    email = data.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="E-mail já cadastrado.")
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    doc = {
        "user_id": user_id, "email": email, "name": data.name.strip(),
        "password_hash": hash_password(data.password), "auth_provider": "email",
        "picture": "", "role": "psicologo", "terms_accepted": True,
        "webhook_token": f"wh_{uuid.uuid4().hex}",
        "created_at": iso(now_utc()),
    }
    await db.users.insert_one(doc)
    token = create_access_token(user_id, email)
    set_auth_cookie(response, token)
    await log_audit(user_id, email, "criar", "usuario", user_id, "Cadastro de profissional")
    return {"user": public_user(doc), "token": token}

@api_router.post("/auth/login")
async def login(data: LoginInput, response: Response):
    email = data.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not user.get("password_hash") or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    token = create_access_token(user["user_id"], email)
    set_auth_cookie(response, token)
    return {"user": public_user(user), "token": token}

@api_router.post("/auth/session")
async def google_session(request: Request, response: Response):
    session_id = request.headers.get("X-Session-ID")
    if not session_id:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id ausente")
    async with httpx.AsyncClient() as http:
        r = await http.get("https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                           headers={"X-Session-ID": session_id})
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Sessão Google inválida")
    d = r.json()
    email = d["email"].lower().strip()
    existing = await db.users.find_one({"email": email})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one({"user_id": user_id},
                                  {"$set": {"name": d.get("name", existing.get("name", "")),
                                            "picture": d.get("picture", "")}})
        user_doc = await db.users.find_one({"user_id": user_id}, {"_id": 0, "password_hash": 0})
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        user_doc = {"user_id": user_id, "email": email, "name": d.get("name", ""),
                    "picture": d.get("picture", ""), "auth_provider": "google",
                    "role": "psicologo", "terms_accepted": True, 
                    "webhook_token": f"wh_{uuid.uuid4().hex}",
                    "created_at": iso(now_utc())}
        await db.users.insert_one(dict(user_doc))
    session_token = d["session_token"]
    await db.user_sessions.insert_one({
        "user_id": user_id, "session_token": session_token,
        "expires_at": iso(now_utc() + timedelta(days=7)), "created_at": iso(now_utc()),
    })
    response.set_cookie(key="session_token", value=session_token, httponly=True, secure=True,
                        samesite="none", max_age=604800, path="/")
    return {"user": public_user(user_doc)}

# Atualize a rota /auth/me para gerar o token retroativamente, se necessário
@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    if not user.get("webhook_token"):
        new_token = f"wh_{uuid.uuid4().hex}"
        await db.users.update_one(
            {"user_id": user["user_id"]}, 
            {"$set": {"webhook_token": new_token}}
        )
        user["webhook_token"] = new_token
    return public_user(user)

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    st = request.cookies.get("session_token")
    if st:
        await db.user_sessions.delete_one({"session_token": st})
    response.delete_cookie("session_token", path="/")
    response.delete_cookie("access_token", path="/")
    return {"ok": True}

# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------
def patient_public(p: dict) -> dict:
    return {
        "id": p["id"], 
        "full_name": p["full_name"],
        "cpf": decrypt_field(p.get("cpf")) or "",
        "rg": decrypt_field(p.get("rg")) or "",
        "birth_date": p.get("birth_date", ""), 
        "age": p.get("age", ""),
        "education": p.get("education", ""),
        "profession": p.get("profession", ""),
        "phone": p.get("phone", ""),
        "email": p.get("email", ""), 
        "address": decrypt_field(p.get("address")) or "",
        "emergency_contact": p.get("emergency_contact", ""),
        "initial_notes": p.get("initial_notes", ""),
        "consent_terms": p.get("consent_terms", False),
        "last_consultation_date": p.get("last_consultation_date", ""),
        "anonymized": p.get("anonymized", False),
        "created_at": p.get("created_at", ""),
    }

@api_router.get("/patients")
async def list_patients(user: dict = Depends(get_current_user)):
    docs = await db.patients.find({"owner_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(1000)
    return [patient_public(p) for p in docs]

@api_router.post("/patients")
async def create_patient(data: PatientInput, user: dict = Depends(get_current_user)):
    pid = f"pat_{uuid.uuid4().hex[:12]}"
    doc = {
        "id": pid, "owner_id": user["user_id"], "full_name": data.full_name.strip(),
        "cpf": encrypt_field(data.cpf), "birth_date": data.birth_date, "phone": data.phone,
        "email": data.email, "emergency_contact": data.emergency_contact,
        "initial_notes": data.initial_notes, "last_consultation_date": "",
        "anonymized": False, "created_at": iso(now_utc()), "updated_at": iso(now_utc()),"rg": encrypt_field(data.rg), 
        "age": data.age,
        "education": data.education,
        "profession": data.profession,
        "address": encrypt_field(data.address),
        "consent_terms": data.consent_terms,
    }
    await db.patients.insert_one(dict(doc))
    await log_audit(user["user_id"], user["email"], "criar", "paciente", pid, f"Paciente {data.full_name}")
    return patient_public(doc)

@api_router.get("/patients/{pid}")
async def get_patient(pid: str, user: dict = Depends(get_current_user)):
    p = await db.patients.find_one({"id": pid, "owner_id": user["user_id"]}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    await log_audit(user["user_id"], user["email"], "visualizar", "paciente", pid, "")
    return patient_public(p)

@api_router.put("/patients/{pid}")
async def update_patient(pid: str, data: PatientInput, user: dict = Depends(get_current_user)):
    p = await db.patients.find_one({"id": pid, "owner_id": user["user_id"]})
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    upd = {
        "full_name": data.full_name.strip(), "cpf": encrypt_field(data.cpf),
        "birth_date": data.birth_date, "phone": data.phone, "email": data.email,
        "emergency_contact": data.emergency_contact, "initial_notes": data.initial_notes,
        "rg": encrypt_field(data.rg),
        "age": data.age,
        "education": data.education,
        "profession": data.profession,
        "address": encrypt_field(data.address),
        "consent_terms": data.consent_terms,
        "updated_at": iso(now_utc()),
    }
    await db.patients.update_one({"id": pid}, {"$set": upd})
    await log_audit(user["user_id"], user["email"], "editar", "paciente", pid, "")
    p2 = await db.patients.find_one({"id": pid}, {"_id": 0})
    return patient_public(p2)

@api_router.delete("/patients/{pid}")
async def delete_patient(pid: str, user: dict = Depends(get_current_user)):
    p = await db.patients.find_one({"id": pid, "owner_id": user["user_id"]})
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    await db.patients.delete_one({"id": pid})
    await db.records.delete_many({"patient_id": pid})
    await log_audit(user["user_id"], user["email"], "excluir", "paciente", pid, "Exclusão definitiva")
    return {"ok": True}

@api_router.post("/patients/{pid}/anonymize")
async def anonymize_patient(pid: str, user: dict = Depends(get_current_user)):
    p = await db.patients.find_one({"id": pid, "owner_id": user["user_id"]})
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    await db.patients.update_one({"id": pid}, {"$set": {
        "full_name": "Paciente Anonimizado", 
        "cpf": encrypt_field(""), 
        "rg": encrypt_field(""),
        "address": encrypt_field(""),
        "phone": "",
        "email": "", 
        "emergency_contact": "", 
        "initial_notes": "",
        "anonymized": True, 
        "updated_at": iso(now_utc()),
    }})
    await log_audit(user["user_id"], user["email"], "anonimizar", "paciente", pid,
                    "Anonimização LGPD (prontuários mantidos p/ guarda legal CFP)")
    p2 = await db.patients.find_one({"id": pid}, {"_id": 0})
    return patient_public(p2)

# ---------------------------------------------------------------------------
# Prontuários (records) — imutáveis com versionamento oculto
# ---------------------------------------------------------------------------
def record_public(r: dict) -> dict:
    return {
        "id": r["id"], "patient_id": r["patient_id"],
        "session_datetime": r.get("session_datetime", ""),
        "content": decrypt_field(r.get("content")) or "",
        "diagnosis": decrypt_field(r.get("diagnosis")) or "",
        "version": r.get("version", 1),
        "created_at": r.get("created_at", ""), "updated_at": r.get("updated_at", ""),
    }

@api_router.get("/patients/{pid}/records")
async def list_records(pid: str, user: dict = Depends(get_current_user)):
    p = await db.patients.find_one({"id": pid, "owner_id": user["user_id"]})
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    docs = await db.records.find({"patient_id": pid, "owner_id": user["user_id"]},
                                 {"_id": 0}).sort("session_datetime", -1).to_list(1000)
    return [record_public(r) for r in docs]

@api_router.post("/patients/{pid}/records")
async def create_record(pid: str, data: RecordInput, user: dict = Depends(get_current_user)):
    p = await db.patients.find_one({"id": pid, "owner_id": user["user_id"]})
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    rid = f"rec_{uuid.uuid4().hex[:12]}"
    doc = {
        "id": rid, "owner_id": user["user_id"], "patient_id": pid,
        "session_datetime": data.session_datetime,
        "content": encrypt_field(data.content), "diagnosis": encrypt_field(data.diagnosis),
        "version": 1, "created_at": iso(now_utc()), "updated_at": iso(now_utc()),
    }
    await db.records.insert_one(dict(doc))
    await db.patients.update_one({"id": pid}, {"$set": {"last_consultation_date": data.session_datetime}})
    await log_audit(user["user_id"], user["email"], "criar", "prontuario", rid, f"Sessão {data.session_datetime}")
    return record_public(doc)

@api_router.put("/records/{rid}")
async def update_record(rid: str, data: RecordInput, user: dict = Depends(get_current_user)):
    r = await db.records.find_one({"id": rid, "owner_id": user["user_id"]})
    if not r:
        raise HTTPException(status_code=404, detail="Prontuário não encontrado")
    # Immutability: snapshot current version into hidden audit collection
    await db.record_versions.insert_one({
        "version_id": f"ver_{uuid.uuid4().hex[:12]}", "record_id": rid,
        "owner_id": user["user_id"], "patient_id": r["patient_id"],
        "session_datetime": r.get("session_datetime", ""),
        "content": r.get("content"), "diagnosis": r.get("diagnosis"),
        "version": r.get("version", 1), "archived_at": iso(now_utc()),
    })
    new_version = r.get("version", 1) + 1
    await db.records.update_one({"id": rid}, {"$set": {
        "session_datetime": data.session_datetime,
        "content": encrypt_field(data.content), "diagnosis": encrypt_field(data.diagnosis),
        "version": new_version, "updated_at": iso(now_utc()),
    }})
    await log_audit(user["user_id"], user["email"], "editar", "prontuario", rid,
                    f"Nova versão v{new_version} (versão anterior arquivada)")
    r2 = await db.records.find_one({"id": rid}, {"_id": 0})
    return record_public(r2)


# ---------------------------------------------------------------------------
# Rota do Webhook Enterprise
# ---------------------------------------------------------------------------

from fastapi import Header

class WebhookPayload(BaseModel):
    patient_data: PatientInput

@api_router.post("/webhook/google-forms")
async def google_forms_webhook(payload: WebhookPayload, x_webhook_token: str = Header(...)):
    # 1. Identifica e Autentica o usuário pelo Token Único (Substitui o e-mail)
    owner = await db.users.find_one({"webhook_token": x_webhook_token})
    
    if not owner:
        raise HTTPException(status_code=401, detail="Token de integração inválido ou revogado")

    # 2. Processamento e Criptografia
    pid = f"pat_{uuid.uuid4().hex[:12]}"
    data = payload.patient_data
    
    doc = {
        "id": pid, 
        "owner_id": owner["user_id"], 
        "full_name": data.full_name.strip(),
        "cpf": encrypt_field(data.cpf), 
        "rg": encrypt_field(data.rg),
        "birth_date": data.birth_date, 
        "age": data.age,
        "education": data.education,
        "profession": data.profession,
        "phone": data.phone, 
        "email": data.email, 
        "address": encrypt_field(data.address),
        "emergency_contact": data.emergency_contact, 
        "initial_notes": data.initial_notes, 
        "consent_terms": data.consent_terms,
        "last_consultation_date": "",
        "anonymized": False, 
        "created_at": iso(now_utc()), 
        "updated_at": iso(now_utc()),
    }
    
    await db.patients.insert_one(dict(doc))
    
    # 3. Trilha de Auditoria
    await log_audit(
        owner["user_id"], 
        owner["email"], 
        "criar", 
        "paciente", 
        pid, 
        f"Integração Google Forms: {data.full_name}"
    )
    
    return {"status": "sucesso", "paciente_id": pid}

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@api_router.get("/dashboard/stats")
async def dashboard_stats(user: dict = Depends(get_current_user)):
    uid = user["user_id"]
    total_patients = await db.patients.count_documents({"owner_id": uid, "anonymized": {"$ne": True}})
    month_start = now_utc().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sessions_month = await db.sessions.count_documents({"owner_id": uid, "start": {"$gte": iso(month_start)}})
    total_records = await db.records.count_documents({"owner_id": uid})
    upcoming = await db.sessions.count_documents({"owner_id": uid, "start": {"$gte": iso(now_utc())}})

    recent_patients_docs = await db.patients.find(
        {"owner_id": uid}, {"_id": 0}).sort("created_at", -1).to_list(5)
    recent_patients = [{"id": p["id"], "full_name": p["full_name"],
                        "birth_date": p.get("birth_date", ""),
                        "created_at": p.get("created_at", "")} for p in recent_patients_docs]

    recent_records_docs = await db.records.find(
        {"owner_id": uid}, {"_id": 0}).sort("updated_at", -1).to_list(6)
    feed = []
    for r in recent_records_docs:
        pat = await db.patients.find_one({"id": r["patient_id"]}, {"_id": 0, "full_name": 1})
        feed.append({
            "record_id": r["id"], "patient_id": r["patient_id"],
            "patient_name": pat["full_name"] if pat else "—",
            "session_datetime": r.get("session_datetime", ""),
            "version": r.get("version", 1),
            "action": "atualizado" if r.get("version", 1) > 1 else "criado",
            "updated_at": r.get("updated_at", ""),
        })
    return {
        "total_patients": total_patients, "sessions_month": sessions_month,
        "total_records": total_records, "upcoming_sessions": upcoming,
        "recent_patients": recent_patients, "records_feed": feed,
    }

# ---------------------------------------------------------------------------
# Agenda (sessions)
# ---------------------------------------------------------------------------
def session_public(s: dict) -> dict:
    return {"id": s["id"], "patient_id": s.get("patient_id", ""),
            "patient_name": s.get("patient_name", ""), "title": s.get("title", ""),
            "start": s.get("start", ""), "end": s.get("end", ""),
            "status": s.get("status", "agendada"), "notes": s.get("notes", "")}

@api_router.get("/sessions")
async def list_sessions(user: dict = Depends(get_current_user)):
    docs = await db.sessions.find({"owner_id": user["user_id"]}, {"_id": 0}).sort("start", 1).to_list(1000)
    return [session_public(s) for s in docs]

@api_router.post("/sessions")
async def create_session(data: SessionInput, user: dict = Depends(get_current_user)):
    sid = f"ses_{uuid.uuid4().hex[:12]}"
    doc = {"id": sid, "owner_id": user["user_id"], **data.model_dump()}
    await db.sessions.insert_one(dict(doc))
    await log_audit(user["user_id"], user["email"], "criar", "agenda", sid, data.title)
    return session_public(doc)

@api_router.put("/sessions/{sid}")
async def update_session(sid: str, data: SessionInput, user: dict = Depends(get_current_user)):
    s = await db.sessions.find_one({"id": sid, "owner_id": user["user_id"]})
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    await db.sessions.update_one({"id": sid}, {"$set": data.model_dump()})
    s2 = await db.sessions.find_one({"id": sid}, {"_id": 0})
    return session_public(s2)

@api_router.delete("/sessions/{sid}")
async def delete_session(sid: str, user: dict = Depends(get_current_user)):
    s = await db.sessions.find_one({"id": sid, "owner_id": user["user_id"]})
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    await db.sessions.delete_one({"id": sid})
    return {"ok": True}

# ---------------------------------------------------------------------------
# Export (JSON / PDF) & Audit
# ---------------------------------------------------------------------------
@api_router.get("/patients/{pid}/export")
async def export_patient(pid: str, format: str = Query("json"), user: dict = Depends(get_current_user)):
    p = await db.patients.find_one({"id": pid, "owner_id": user["user_id"]}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    records = await db.records.find({"patient_id": pid, "owner_id": user["user_id"]},
                                    {"_id": 0}).sort("session_datetime", -1).to_list(1000)
    pat = patient_public(p)
    recs = [record_public(r) for r in records]
    await log_audit(user["user_id"], user["email"], "exportar", "paciente", pid, f"Formato {format}")

    if format == "json":
        payload = {"paciente": pat, "prontuarios": recs,
                   "exportado_em": iso(now_utc()), "profissional": user["email"]}
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return StreamingResponse(io.BytesIO(data), media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="prontuario_{pid}.json"'})

    # PDF
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    navy = colors.HexColor("#1E3A8A")
    h = ParagraphStyle("h", parent=styles["Heading1"], textColor=navy, fontSize=18)
    sub = ParagraphStyle("sub", parent=styles["Heading2"], textColor=colors.HexColor("#334155"), fontSize=12)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=15,
                          textColor=colors.HexColor("#0F172A"))
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#64748B"))
    elems = [Paragraph("Prontuário Clínico — PsiGestão", h), Spacer(1, 4)]
    elems.append(Paragraph(f"Profissional: {user['email']}", meta))
    elems.append(Paragraph(f"Emitido em: {now_utc().strftime('%d/%m/%Y %H:%M UTC')}", meta))
    elems.append(Spacer(1, 8))
    elems.append(HRFlowable(width="100%", color=navy))
    elems.append(Spacer(1, 8))
    elems.append(Paragraph("Dados do Paciente", sub))
    elems.append(Paragraph(f"<b>Nome:</b> {pat['full_name']}", body))
    elems.append(Paragraph(f"<b>CPF:</b> {pat['cpf'] or '—'}", body))
    elems.append(Paragraph(f"<b>Nascimento:</b> {pat['birth_date'] or '—'}", body))
    elems.append(Paragraph(f"<b>Telefone:</b> {pat['phone'] or '—'}", body))
    elems.append(Paragraph(f"<b>E-mail:</b> {pat['email'] or '—'}", body))
    elems.append(Spacer(1, 10))
    elems.append(Paragraph("Evolução Clínica", sub))
    if not recs:
        elems.append(Paragraph("Nenhum prontuário registrado.", body))
    for r in recs:
        elems.append(Spacer(1, 6))
        elems.append(Paragraph(f"<b>Sessão:</b> {r['session_datetime']}  (v{r['version']})", meta))
        if r["diagnosis"]:
            elems.append(Paragraph(f"<b>Diagnóstico:</b> {r['diagnosis']}", body))
        txt = (r["content"] or "").replace("\n", "<br/>")
        elems.append(Paragraph(txt, body))
        elems.append(HRFlowable(width="100%", color=colors.HexColor("#E2E8F0")))
    doc.build(elems)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="prontuario_{pid}.pdf"'})

@api_router.get("/audit")
async def get_audit(user: dict = Depends(get_current_user)):
    docs = await db.audit_logs.find({"owner_id": user["user_id"]}, {"_id": 0}).sort("timestamp", -1).to_list(100)
    return docs

@api_router.get("/")
async def root():
    return {"message": "PsiGestão API online"}

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("user_id")
    await db.user_sessions.create_index("session_token")
    await db.patients.create_index("owner_id")
    await db.records.create_index([("owner_id", 1), ("patient_id", 1)])
    await db.sessions.create_index("owner_id")
    admin_email = os.environ.get("ADMIN_EMAIL", "").lower().strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if admin_email and admin_password:
        existing = await db.users.find_one({"email": admin_email})
        if not existing:
            await db.users.insert_one({
                "user_id": f"user_{uuid.uuid4().hex[:12]}", "email": admin_email,
                "name": "Administrador", "password_hash": hash_password(admin_password),
                "auth_provider": "email", "picture": "", "role": "psicologo",
                "terms_accepted": True, "created_at": iso(now_utc()),
            })
            logger.info("Admin seeded: %s", admin_email)
        elif existing.get("password_hash") and not verify_password(admin_password, existing["password_hash"]):
            await db.users.update_one({"email": admin_email},
                                      {"$set": {"password_hash": hash_password(admin_password)}})

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FRONTEND_URL", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
