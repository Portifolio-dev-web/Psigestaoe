import base64
import hashlib
import io
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import bcrypt
import httpx
import jwt
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db, engine, Base
from models import AuditLog, Patient, Record, RecordVersion, Session, User, UserSession, now_utc

# -----------------------------------------------------------
# Configurações Iniciais (Mantenha as suas variáveis de ambiente aqui)
# -----------------------------------------------------------
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret")
JWT_ALGORITHM = "HS256"

# 1. Inicializa o app UMA ÚNICA VEZ
app = FastAPI(title="PsiGestão API")

# 1. Cria as tabelas no banco de dados todas as tabelas que ainda não existem
@app.on_event("startup")
async def init_tables():
    # Isso instrui o SQLAlchemy a criar todas as tabelas que ainda não existem
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# 2. Configura o CORS logo em seguida
origins = [
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Cria e inclui as suas rotas
api_router = APIRouter(prefix="/api")


# 4. Configuração de logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("psigestao")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(key="access_token", value=token, httponly=True, samesite="lax", path="/", secure=False)


def _get_fernet_key() -> bytes:
    raw_key = os.environ.get("FERNET_KEY")
    if raw_key:
        if len(raw_key) == 44:
            return raw_key.encode("utf-8")
        return base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
    fallback = base64.urlsafe_b64encode(hashlib.sha256((JWT_SECRET or "dev-secret").encode("utf-8")).digest())
    return fallback


def encrypt_field(value: Optional[str]) -> str:
    if value is None or value == "":
        return ""
    fernet = Fernet(_get_fernet_key())
    return fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_field(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        fernet = Fernet(_get_fernet_key())
        return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except Exception:
        return value

# ---------------------------------------------------------------------------
# Modelos Pydantic (Atualizados para Tipagem Nativa de Data)
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
    rg: Optional[str] = ""
    birth_date: Optional[date] = None # Tipagem alterada para date nativo
    age: Optional[str] = ""
    education: Optional[str] = ""
    profession: Optional[str] = ""
    phone: Optional[str] = ""
    email: Optional[str] = ""
    address: Optional[str] = ""
    emergency_contact: Optional[str] = ""
    initial_notes: Optional[str] = ""
    consent_terms: bool = False

class RecordInput(BaseModel):
    session_datetime: datetime # Tipagem alterada para datetime nativo
    content: str
    diagnosis: Optional[str] = ""

class SessionInput(BaseModel):
    patient_id: Optional[str] = ""
    patient_name: Optional[str] = ""
    title: str
    start: datetime # Tipagem alterada para datetime nativo
    end: Optional[datetime] = None # Tipagem alterada para datetime nativo
    status: str = "agendada"
    notes: Optional[str] = ""


# ---------------------------------------------------------------------------
# Funções Auxiliares de Serialização (Pacientes e Prontuários)
# ---------------------------------------------------------------------------
def patient_public(p: Patient) -> dict:
    return {
        "id": p.id, 
        "full_name": p.full_name,
        "cpf": decrypt_field(p.cpf) if p.cpf else "",
        "rg": decrypt_field(p.rg) if p.rg else "",
        # Usamos .isoformat() para transformar objetos Date em string "YYYY-MM-DD" para o JSON
        "birth_date": p.birth_date.isoformat() if p.birth_date else "", 
        "age": p.age or "",
        "education": p.education or "",
        "profession": p.profession or "",
        "phone": p.phone or "",
        "email": p.email or "", 
        "address": decrypt_field(p.address) if p.address else "",
        "emergency_contact": p.emergency_contact or "",
        "initial_notes": p.initial_notes or "",
        "consent_terms": p.consent_terms or False,
        "last_consultation_date": p.last_consultation_date or "",
        "anonymized": p.anonymized or False,
        # Trata o DateTime com timezone para retornar string ISO completa
        "created_at": p.created_at.isoformat() if p.created_at else "",
    }

def record_public(r: Record) -> dict:
    return {
        "id": r.id, 
        "patient_id": r.patient_id,
        # Conversão segura do DateTime para string ISO 8601
        "session_datetime": r.session_datetime.isoformat() if r.session_datetime else "",
        "content": decrypt_field(r.content) if r.content else "",
        "diagnosis": decrypt_field(r.diagnosis) if r.diagnosis else "",
        "version": r.version or 1,
        "created_at": r.created_at.isoformat() if r.created_at else "", 
        "updated_at": r.updated_at.isoformat() if r.updated_at else "",
    }

# ---------------------------------------------------------------------------
# Funções Auxiliares e Auditoria
# ---------------------------------------------------------------------------

def public_user(u: User) -> dict:
    return {
        "user_id": u.user_id, 
        "email": u.email, 
        "name": u.name or "",
        "picture": u.picture or "", 
        "auth_provider": u.auth_provider or "email",
        "terms_accepted": u.terms_accepted,
        "webhook_token": u.webhook_token
    }

# Refatoração da trilha de auditoria para usar o SQLAlchemy
async def log_audit(db: AsyncSession, owner_id: str, user_email: str, action: str, entity_type: str,
                    entity_id: str, detail: str = ""):
    novo_log = AuditLog(
        owner_id=owner_id,
        user_email=user_email,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail
    )
    db.add(novo_log)
    await db.commit()

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    # 1) Sessão do Google
    session_token = request.cookies.get("session_token")
    if not session_token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            session_token = auth[7:]
            
    if session_token:
        stmt = select(UserSession).where(UserSession.session_token == session_token)
        result = await db.execute(stmt)
        sess = result.scalar_one_or_none()
        
        if sess and sess.expires_at >= now_utc():
            stmt_user = select(User).where(User.user_id == sess.user_id)
            result_user = await db.execute(stmt_user)
            user = result_user.scalar_one_or_none()
            if user:
                return user

    # 2) Token JWT (E-mail/Senha)
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            
    if token:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            stmt_user = select(User).where(User.user_id == payload["sub"])
            result_user = await db.execute(stmt_user)
            user = result_user.scalar_one_or_none()
            if user:
                return user
        except jwt.PyJWTError:
            pass
    
    raise HTTPException(status_code=401, detail="Usuário não autenticado")
# ---------------------------------------------------------------------------

@api_router.post("/auth/register")
async def register_user(data: RegisterInput, response: Response, db: AsyncSession = Depends(get_db)):
    if not data.terms_accepted:
        raise HTTPException(status_code=400, detail="É necessário aceitar os Termos de Privacidade (LGPD).")
    
    email = data.email.lower().strip()
    
    # Verifica se já existe
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="E-mail já cadastrado.")
    
    # Cria o novo usuário
    new_user = User(
        email=email,
        name=data.name.strip(),
        password_hash=hash_password(data.password),
        auth_provider="email",
        terms_accepted=True
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user) # Atualiza o objeto com os dados gerados pelo banco (ex: user_id)
    
    token = create_access_token(new_user.user_id, email)
    set_auth_cookie(response, token)
    
    await log_audit(db, new_user.user_id, email, "criar", "usuario", new_user.user_id, "Cadastro de profissional")
    
    return {"user": public_user(new_user), "token": token}

@api_router.post("/auth/login")
async def login(data: LoginInput, response: Response, db: AsyncSession = Depends(get_db)):
    email = data.email.lower().strip()
    
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user or not user.password_hash or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    
    token = create_access_token(user.user_id, email)
    set_auth_cookie(response, token)
    
    return {"user": public_user(user), "token": token}

@api_router.post("/auth/session")
async def google_session(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
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
    
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if user:
        # Atualiza o usuário existente
        user.name = d.get("name", user.name)
        user.picture = d.get("picture", user.picture)
    else:
        # Cria novo usuário via Google
        user = User(
            email=email,
            name=d.get("name", ""),
            picture=d.get("picture", ""),
            auth_provider="google",
            terms_accepted=True
        )
        db.add(user)
    
    await db.commit()
    await db.refresh(user)
    
    session_token = d["session_token"]
    
    # Salva a sessão do usuário
    new_session = UserSession(
        session_token=session_token,
        user_id=user.user_id,
        expires_at=now_utc() + timedelta(days=7)
    )
    db.add(new_session)
    await db.commit()
    
    response.set_cookie(key="session_token", value=session_token, httponly=True, secure=True,
                        samesite="none", max_age=604800, path="/")
                        
    return {"user": public_user(user)}

@api_router.get("/auth/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.webhook_token:
        user.webhook_token = f"wh_{uuid.uuid4().hex}"
        await db.commit()
        await db.refresh(user)
        
    return public_user(user)

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    st = request.cookies.get("session_token")
    if st:
        stmt = select(UserSession).where(UserSession.session_token == st)
        result = await db.execute(stmt)
        sess = result.scalar_one_or_none()
        
        if sess:
            await db.delete(sess)
            await db.commit()
            
    response.delete_cookie("session_token", path="/")
    response.delete_cookie("access_token", path="/")
    return {"ok": True}

# ---------------------------------------------------------------------------
# Rotas de Pacientes e Prontuários
# ---------------------------------------------------------------------------

@api_router.get("/patients")
async def list_patients(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Patient).where(Patient.owner_id == user.user_id).order_by(desc(Patient.created_at))
    result = await db.execute(stmt)
    docs = result.scalars().all()
    
    # Adaptamos a função patient_public para ler atributos do objeto (p.full_name) em vez de chaves (p["full_name"])
    return [patient_public(p) for p in docs]

@api_router.get("/patients/{pid}")
async def get_patient(pid: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Busca o paciente pelo ID, garantindo que pertence ao usuário logado
    stmt = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    result = await db.execute(stmt)
    p = result.scalar_one_or_none()
    
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
        
    return patient_public(p)

@api_router.post("/patients")
async def create_patient(data: PatientInput, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    novo_paciente = Patient(
        owner_id=user.user_id,
        full_name=data.full_name.strip(),
        cpf=encrypt_field(data.cpf),
        rg=encrypt_field(data.rg),
        birth_date=data.birth_date,
        age=data.age,
        education=data.education,
        profession=data.profession,
        phone=data.phone,
        email=data.email,
        address=encrypt_field(data.address),
        emergency_contact=data.emergency_contact,
        initial_notes=data.initial_notes,
        consent_terms=data.consent_terms,
        anonymized=False
    )
    db.add(novo_paciente)
    await db.commit()
    await db.refresh(novo_paciente)
    
    await log_audit(db, user.user_id, user.email, "criar", "paciente", novo_paciente.id, f"Paciente {data.full_name}")
    return patient_public(novo_paciente)

@api_router.put("/records/{rid}")
async def update_record(rid: str, data: RecordInput, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Record).where(Record.id == rid, Record.owner_id == user.user_id)
    result = await db.execute(stmt)
    r = result.scalar_one_or_none()
    
    if not r:
        raise HTTPException(status_code=404, detail="Prontuário não encontrado")
    
    # Immutability: arquivando a versão atual na tabela record_versions
    versao_antiga = RecordVersion(
        record_id=r.id,
        owner_id=r.owner_id,
        patient_id=r.patient_id,
        session_datetime=r.session_datetime,
        content=r.content,
        diagnosis=r.diagnosis,
        version=r.version
    )
    db.add(versao_antiga)
    
    # Atualizando o registro principal
    r.session_datetime = data.session_datetime
    r.content = encrypt_field(data.content)
    r.diagnosis = encrypt_field(data.diagnosis)
    r.version += 1
    r.updated_at = now_utc()
    
    await db.commit()
    await db.refresh(r)
    
    await log_audit(db, user.user_id, user.email, "editar", "prontuario", r.id, f"Nova versão v{r.version}")
    return record_public(r)

@api_router.delete("/patients/{pid}")
async def delete_patient(pid: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    p = (await db.execute(stmt)).scalar_one_or_none()
    
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
        
    await db.delete(p)
    await db.commit() # O CASCADE no PostgreSQL deletará os records automaticamente
    
    await log_audit(db, user.user_id, user.email, "excluir", "paciente", pid, "Exclusão definitiva")
    return {"ok": True}

@api_router.post("/patients/{pid}/anonymize")
async def anonymize_patient(pid: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    p = (await db.execute(stmt)).scalar_one_or_none()
    
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
        
    p.full_name = "Paciente Anonimizado"
    p.cpf = encrypt_field("")
    p.rg = encrypt_field("")
    p.address = encrypt_field("")
    p.phone = ""
    p.email = ""
    p.emergency_contact = ""
    p.initial_notes = ""
    p.anonymized = True
    p.updated_at = now_utc()
    
    await db.commit()
    await db.refresh(p)
    
    await log_audit(db, user.user_id, user.email, "anonimizar", "paciente", pid,
                    "Anonimização LGPD (prontuários mantidos p/ guarda legal CFP)")
                    
    return patient_public(p)

@api_router.get("/patients/{pid}/records")
async def list_records(pid: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Verifica se o paciente existe e pertence ao usuário
    stmt_p = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    p = (await db.execute(stmt_p)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
        
    stmt_r = select(Record).where(Record.patient_id == pid, Record.owner_id == user.user_id).order_by(desc(Record.session_datetime))
    docs = (await db.execute(stmt_r)).scalars().all()
    
    return [record_public(r) for r in docs]

@api_router.post("/patients/{pid}/records")
async def create_record(pid: str, data: RecordInput, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt_p = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    p = (await db.execute(stmt_p)).scalar_one_or_none()
    
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
        
    novo_prontuario = Record(
        owner_id=user.user_id,
        patient_id=pid,
        session_datetime=data.session_datetime, # DateTime nativo do Pydantic
        content=encrypt_field(data.content),
        diagnosis=encrypt_field(data.diagnosis),
        version=1
    )
    
    # Atualiza a data da última consulta no paciente
    p.last_consultation_date = data.session_datetime.isoformat()
    
    db.add(novo_prontuario)
    await db.commit()
    await db.refresh(novo_prontuario)
    
    await log_audit(db, user.user_id, user.email, "criar", "prontuario", novo_prontuario.id, f"Sessão {data.session_datetime.isoformat()}")
    return record_public(novo_prontuario)

@api_router.get("/patients/{pid}/export")
async def export_patient(pid: str, format: str = Query("json"), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt_p = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    p = (await db.execute(stmt_p)).scalar_one_or_none()
    
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
        
    stmt_r = select(Record).where(Record.patient_id == pid, Record.owner_id == user.user_id).order_by(desc(Record.session_datetime))
    records = (await db.execute(stmt_r)).scalars().all()
    
    pat = patient_public(p)
    recs = [record_public(r) for r in records]
    
    await log_audit(db, user.user_id, user.email, "exportar", "paciente", pid, f"Formato {format}")

    if format == "json":
        payload = {"paciente": pat, "prontuarios": recs,
                   "exportado_em": now_utc().isoformat(), "profissional": user.email}
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return StreamingResponse(io.BytesIO(data), media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="prontuario_{pid}.json"'})

    # PDF Builder (A lógica original do reportlab do seu código se mantém igual aqui)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    navy = colors.HexColor("#1E3A8A")
    h = ParagraphStyle("h", parent=styles["Heading1"], textColor=navy, fontSize=18)
    sub = ParagraphStyle("sub", parent=styles["Heading2"], textColor=colors.HexColor("#334155"), fontSize=12)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=15, textColor=colors.HexColor("#0F172A"))
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#64748B"))
    
    elems = [Paragraph("Prontuário Clínico — PsiGestão", h), Spacer(1, 4)]
    elems.append(Paragraph(f"Profissional: {user.email}", meta))
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
async def get_audit(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(AuditLog).where(AuditLog.owner_id == user.user_id).order_by(desc(AuditLog.timestamp)).limit(100)
    docs = (await db.execute(stmt)).scalars().all()
    
    return [{
        "log_id": a.log_id,
        "action": a.action,
        "entity_type": a.entity_type,
        "entity_id": a.entity_id,
        "detail": a.detail,
        "timestamp": a.timestamp.isoformat() if a.timestamp else ""
    } for a in docs]

# Webhook do Google Forms
from fastapi import Header

class WebhookPayload(BaseModel):
    patient_data: PatientInput

@api_router.post("/webhook/google-forms")
async def google_forms_webhook(payload: WebhookPayload, x_webhook_token: str = Header(...), db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.webhook_token == x_webhook_token)
    owner = (await db.execute(stmt)).scalar_one_or_none()
    
    if not owner:
        raise HTTPException(status_code=401, detail="Token de integração inválido ou revogado")

    data = payload.patient_data
    
    novo_paciente = Patient(
        owner_id=owner.user_id,
        full_name=data.full_name.strip(),
        cpf=encrypt_field(data.cpf),
        rg=encrypt_field(data.rg),
        birth_date=data.birth_date, # Date nativo
        age=data.age,
        education=data.education,
        profession=data.profession,
        phone=data.phone,
        email=data.email,
        address=encrypt_field(data.address),
        emergency_contact=data.emergency_contact,
        initial_notes=data.initial_notes,
        consent_terms=data.consent_terms,
        anonymized=False
    )
    
    db.add(novo_paciente)
    await db.commit()
    await db.refresh(novo_paciente)
    
    await log_audit(db, owner.user_id, owner.email, "criar", "paciente", novo_paciente.id, f"Integração Google Forms: {data.full_name}")
    
    return {"status": "sucesso", "paciente_id": novo_paciente.id}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@api_router.get("/dashboard/stats")
async def dashboard_stats(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    uid = user.user_id
    
    # Contagens usando func.count()
    stmt_pacientes = select(func.count()).select_from(Patient).where(Patient.owner_id == uid, Patient.anonymized == False)
    total_patients = await db.scalar(stmt_pacientes)
    
    month_start = now_utc().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Como start_time agora é DateTime nativo, comparamos diretamente com month_start (removida a função iso())
    stmt_sessoes = select(func.count()).select_from(Session).where(Session.owner_id == uid, Session.start_time >= month_start)
    sessions_month = await db.scalar(stmt_sessoes)
    
    stmt_records = select(func.count()).select_from(Record).where(Record.owner_id == uid)
    total_records = await db.scalar(stmt_records)
    
    # --- LÓGICA DO BÔNUS APLICADA AQUI ---
    # Conta sessões agendadas que vão ocorrer de hoje em diante
    stmt_upcoming = select(func.count()).select_from(Session).where(Session.owner_id == uid, Session.start_time >= now_utc())
    upcoming = await db.scalar(stmt_upcoming)
    
    # Listagens recentes (LIMIT)
    stmt_recent_pat = select(Patient).where(Patient.owner_id == uid).order_by(desc(Patient.created_at)).limit(5)
    recent_patients_docs = (await db.execute(stmt_recent_pat)).scalars().all()
    
    # Uso do .isoformat() para garantir que o objeto date/datetime seja convertido para string no JSON
    recent_patients = [{
        "id": p.id, 
        "full_name": p.full_name, 
        "birth_date": p.birth_date.isoformat() if p.birth_date else "", 
        "created_at": p.created_at.isoformat() if p.created_at else ""
    } for p in recent_patients_docs]
    
    # Feed de registros com JOIN implícito via ORM
    stmt_recent_rec = select(Record).options(selectinload(Record.patient)).where(Record.owner_id == uid).order_by(desc(Record.updated_at)).limit(6)
    recent_records_docs = (await db.execute(stmt_recent_rec)).scalars().all()
    
    feed = []
    for r in recent_records_docs:
        feed.append({
            "record_id": r.id, 
            "patient_id": r.patient_id,
            "patient_name": r.patient.full_name if r.patient else "—",
            "session_datetime": r.session_datetime.isoformat() if r.session_datetime else "",
            "version": r.version,
            "action": "atualizado" if r.version > 1 else "criado",
            "updated_at": r.updated_at.isoformat() if r.updated_at else "",
        })
        
    return {
        "total_patients": total_patients, 
        "sessions_month": sessions_month,
        "total_records": total_records, 
        "upcoming_sessions": upcoming, # Variável populada com a query dinâmica
        "recent_patients": recent_patients, 
        "records_feed": feed,
    }
# ---------------------------------------------------------------------------
# Funções Auxiliares de Serialização (Agenda)
# ---------------------------------------------------------------------------
def session_public(s: Session) -> dict:
    return {
        "id": s.id, 
        "patient_id": s.patient_id or "",
        "patient_name": s.patient_name or "", 
        "title": s.title,
        # O Frontend espera "start" e "end", mas no banco chamamos de start_time e end_time
        "start": s.start_time.isoformat() if s.start_time else "", 
        "end": s.end_time.isoformat() if s.end_time else "",
        "status": s.status or "agendada", 
        "notes": s.notes or ""
    }

# ---------------------------------------------------------------------------
# Rotas da Agenda (Sessions)
# ---------------------------------------------------------------------------
@api_router.get("/sessions")
async def list_sessions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Ordena as sessões pela data de início (crescente) para formar a agenda corretamente
    stmt = select(Session).where(Session.owner_id == user.user_id).order_by(Session.start_time.asc())
    result = await db.execute(stmt)
    docs = result.scalars().all()
    
    return [session_public(s) for s in docs]

@api_router.post("/sessions")
async def create_session(data: SessionInput, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    nova_sessao = Session(
        owner_id=user.user_id,
        patient_id=data.patient_id,
        patient_name=data.patient_name,
        title=data.title,
        start_time=data.start, # Pydantic já entregou um datetime
        end_time=data.end,     # Pydantic já entregou um datetime ou None
        status=data.status,
        notes=data.notes
    )
    db.add(nova_sessao)
    await db.commit()
    await db.refresh(nova_sessao)
    
    await log_audit(db, user.user_id, user.email, "criar", "agenda", nova_sessao.id, data.title)
    return session_public(nova_sessao)

@api_router.put("/sessions/{sid}")
async def update_session(sid: str, data: SessionInput, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Session).where(Session.id == sid, Session.owner_id == user.user_id)
    result = await db.execute(stmt)
    s = result.scalar_one_or_none()
    
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
        
    s.patient_id = data.patient_id
    s.patient_name = data.patient_name
    s.title = data.title
    s.start_time = data.start
    s.end_time = data.end
    s.status = data.status
    s.notes = data.notes
    
    await db.commit()
    await db.refresh(s)
    
    return session_public(s)

@api_router.delete("/sessions/{sid}")
async def delete_session(sid: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Session).where(Session.id == sid, Session.owner_id == user.user_id)
    result = await db.execute(stmt)
    s = result.scalar_one_or_none()
    
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
        
    await db.delete(s)
    await db.commit()
    
    return {"ok": True}

app.include_router(api_router)