import io
import json
import logging
import os
import re
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from fastapi import Response

import bcrypt
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import jwt
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from xml.sax.saxutils import escape

from crypto import is_fernet_token, load_fernet
from rate_limit import enforce, rate_limit
from database import get_db, engine, Base
from models import AuditLog, Patient, Record, RecordVersion, Session, User, UserSession, now_utc

# -----------------------------------------------------------
# Configurações Iniciais (Mantenha as suas variáveis de ambiente aqui)
# -----------------------------------------------------------
# -----------------------------------------------------------
# Configurações Iniciais e Validação de Segurança
# -----------------------------------------------------------
# Define o ambiente. Em produção, a variável de ambiente DEVE ser configurada como "production".
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_ALGORITHM = "HS256"

# Validação Fail-Fast do JWT
if not JWT_SECRET:
    if ENVIRONMENT == "production":
        logging.critical("SECURITY ALERT: JWT_SECRET ausente em produção. Abortando inicialização.")
        sys.exit(1)
    else:
        logging.warning("Usando JWT_SECRET inseguro para desenvolvimento.")
        JWT_SECRET = "dev-secret"

# CORREÇÃO DE SEGURANÇA (item 2 da auditoria):
# A chave de criptografia agora vem SOMENTE de FERNET_KEY (+ FERNET_OLD_KEYS
# durante uma rotação), é validada na inicialização e é independente do
# JWT_SECRET. O objeto é criado uma única vez, e não a cada campo como antes.
# Detalhes e regras de fallback em crypto.py.
_fernet = load_fernet(ENVIRONMENT)


def set_auth_cookie(response: Response, token: str) -> None:
    """
    Configura o cookie de autenticação com restrições baseadas no ambiente.

    CORREÇÃO DE SEGURANÇA (item 1 da auditoria): esta função era sobrescrita por
    uma cópia mais abaixo no arquivo que fixava secure=False e samesite="lax".
    A cópia foi removida, então esta é a versão que vale de fato.

    ATENÇÃO: com ENVIRONMENT=production o cookie passa a ser `secure`, ou seja,
    o navegador só o envia por HTTPS. Em produção o app PRECISA estar atrás de HTTPS.
    """
    is_prod = ENVIRONMENT == "production"
    
    response.set_cookie(
        key="access_token", 
        value=token, 
        httponly=True, 
        # Em produção, SameSite="strict" mitiga ataques CSRF de forma agressiva.
        samesite="strict" if is_prod else "lax", 
        path="/", 
        # Em produção, obriga o navegador a enviar o cookie apenas via HTTPS.
        secure=is_prod 
    )

# 1. Inicializa o app UMA ÚNICA VEZ
app = FastAPI(title="PsiGestão API")

# 1. Cria as tabelas no banco de dados todas as tabelas que ainda não existem
@app.on_event("startup")
async def init_tables():
    # Isso instrui o SQLAlchemy a criar todas as tabelas que ainda não existem
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# 2. Configura o CORS logo em seguida
# CORREÇÃO DE SEGURANÇA (item 11 da auditoria):
# Antes, a lista estava fixa em localhost:3000 e a variável CORS_ORIGINS do .env
# era ignorada: em produção o frontend real seria bloqueado, e a "solução" comum
# seria liberar "*" no código. Agora as origens vêm de CORS_ORIGINS
# (separadas por vírgula, ex.: "https://app.psigestao.com.br,https://www.psigestao.com.br").
#
# "*" é incompatível com cookies de login (allow_credentials=True): o navegador
# recusa essa combinação. Por isso:
#   - produção: "*" ou lista vazia derruba a inicialização (fail-fast);
#   - desenvolvimento: "*" ou lista vazia vira localhost:3000, que é o que o
#     .env de desenvolvimento espera, então nada muda no fluxo local.
DEV_CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def _load_cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "").strip().strip('"')
    parsed = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]

    if not parsed or "*" in parsed:
        if ENVIRONMENT == "production":
            logging.critical("SECURITY ALERT: defina CORS_ORIGINS com os domínios reais do frontend (sem '*').")
            sys.exit(1)
        return DEV_CORS_ORIGINS
    return parsed


origins = _load_cors_origins()

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


# CORREÇÃO DE SEGURANÇA (item 8 da auditoria): sessões revogáveis.
# Antes, o JWT valia 7 dias e não havia como invalidá-lo: o "Sair" só apagava
# o cookie do navegador. Um token copiado continuava funcionando por uma semana.
#
# Agora cada login cria uma linha em `user_sessions`, e o JWT carrega o id dessa
# sessão (`sid`). A cada requisição, get_current_user confere se a sessão ainda
# existe e não expirou. O logout apaga a linha, e o token morre na hora, em
# qualquer lugar onde tenha sido copiado.
#
# A duração caiu de 7 dias para 12 horas (um dia de atendimento), ajustável
# por JWT_EXPIRE_HOURS. Para um sistema com prontuários, sessões curtas reduzem
# o estrago de um computador de consultório esquecido logado.
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "12"))


async def open_login_session(db: AsyncSession, user: User) -> str:
    """Cria a sessão no banco e devolve o JWT assinado que aponta para ela.

    Não renomeie para `create_session`: esse nome já é a rota POST /sessions
    da agenda, e a definição de baixo sobrescreveria esta (o mesmo tipo de
    bug corrigido no item 1).
    """
    expires_at = now_utc() + timedelta(hours=JWT_EXPIRE_HOURS)
    session_id = secrets.token_urlsafe(32)
    db.add(UserSession(session_token=session_id, user_id=user.user_id, expires_at=expires_at))
    await db.commit()

    payload = {
        "sub": user.user_id,
        "sid": session_id,
        "exp": expires_at,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# CORREÇÃO DE SEGURANÇA (item 1 da auditoria):
# Neste ponto existiam SEGUNDAS definições de `set_auth_cookie` e `_get_fernet_key`.
# Em Python a última definição vence, então elas anulavam as versões seguras do
# topo do arquivo: o cookie saía sempre com secure=False e a validação da chave
# de criptografia nunca rodava. As duplicatas foram removidas; as únicas
# definições agora são `set_auth_cookie` (topo do arquivo) e `load_fernet` (crypto.py).


def encrypt_field(value: Optional[str]) -> str:
    if value is None or value == "":
        return ""
    # Sempre criptografa com a chave PRIMÁRIA (FERNET_KEY).
    return _fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_field(value: Optional[str]) -> str:
    if not value:
        return ""

    # Valor legado gravado em texto puro (antes da criptografia existir).
    # Devolvemos como está; o script rotate_encryption_key.py criptografa esses registros.
    if not is_fernet_token(value):
        logger.warning("Campo sensível encontrado sem criptografia. Rode rotate_encryption_key.py.")
        return value

    try:
        return _fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # CORREÇÃO (item 2): antes, qualquer erro devolvia o texto CIFRADO em silêncio.
        # A tela mostrava lixo, nada aparecia no log, e se o usuário salvasse o
        # formulário esse lixo era criptografado de novo, corrompendo o dado de vez.
        # Agora a falha é explícita: registra no log e interrompe a requisição.
        logger.error("Falha ao descriptografar campo sensível: FERNET_KEY incorreta ou dado corrompido.")
        raise HTTPException(
            status_code=500,
            detail="Não foi possível descriptografar os dados. Verifique a configuração da chave de criptografia.",
        )

# ---------------------------------------------------------------------------
# Modelos Pydantic (Atualizados para Tipagem Nativa de Data)
# ---------------------------------------------------------------------------

# CORREÇÃO DE SEGURANÇA (item 10 da auditoria): política de senha.
# Antes, qualquer senha era aceita, inclusive "1" ou vazia.
# Regras (alinhadas ao NIST SP 800-63B, que prioriza comprimento):
#   - mínimo de 8 caracteres, com pelo menos 1 letra e 1 número;
#   - máximo de 72 BYTES: o bcrypt ignora tudo depois do byte 72 (e versões
#     novas da lib levantam erro), então senhas maiores dariam falsa segurança.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72


def validate_password_strength(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"A senha deve ter pelo menos {PASSWORD_MIN_LENGTH} caracteres.")
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError("A senha é longa demais (máximo de 72 caracteres).")
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise ValueError("A senha deve conter letras e números.")
    return password


class RegisterInput(BaseModel):
    name: str
    email: EmailStr
    password: str
    terms_accepted: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("Informe um nome entre 1 e 120 caracteres.")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_strength(value)

class LoginInput(BaseModel):
    email: EmailStr
    # No login NÃO aplicamos a política (contas antigas podem ter senhas fora
    # dela); só limitamos o tamanho para não gastar bcrypt com payloads enormes.
    password: str = Field(max_length=256)


class ProfileInput(BaseModel):
    """Dados do profissional usados no termo de consentimento (item 6)."""
    name: str
    crp: Optional[str] = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("Informe um nome entre 1 e 120 caracteres.")
        return value

    @field_validator("crp")
    @classmethod
    def validate_crp(cls, value: Optional[str]) -> str:
        # Formato do registro no Conselho Regional de Psicologia: "RR/NNNNNN" (ex.: 06/123456).
        value = (value or "").strip()
        if value and not re.fullmatch(r"\d{2}/\d{1,6}", value):
            raise ValueError("CRP inválido. Use o formato 06/123456.")
        return value

class GoogleAuthInput(BaseModel):
    credential: str # O ID Token JWT retornado pelo Google Identity Services no frontend

class PatientInput(BaseModel):
    full_name: str
    cpf: Optional[str] = ""
    rg: Optional[str] = ""
    birth_date: Optional[date] = None # Tipagem alterada para date nativo
    age: Optional[str] = ""
    education: Optional[str] = ""
    marital_status: Optional[str] = ""
    profession: Optional[str] = ""
    phone: Optional[str] = ""
    email: Optional[str] = ""
    address: Optional[str] = ""
    emergency_contact: Optional[str] = ""
    initial_notes: Optional[str] = ""
    consent_terms: bool = False

    @field_validator("birth_date", mode="before")
    @classmethod
    def normalize_birth_date(cls, value):
        if isinstance(value, str):
            value = value.strip()
            parts = value.replace("/", "-").split("-")
            if len(parts) == 3 and len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 4:
                try:
                    return date(int(parts[2]), int(parts[1]), int(parts[0]))
                except ValueError:
                    return value
        return value


# CORREÇÃO (item 6 da auditoria): termo de consentimento por profissional.
# Antes o texto era uma constante com o nome e o CRP de UMA psicóloga
# ("Fernanda Andrade Da Silva – CRP 06/228054"), exibido e exportado em PDF
# para os pacientes de TODOS os profissionais cadastrados. Agora o texto é
# montado com o nome e o CRP do dono do paciente (cadastrados em
# Configurações -> Dados profissionais, rota PUT /api/auth/profile).
CONSENT_TERM_TEMPLATE = (
    "TERMO DE CONSENTIMENTO LIVRE E ESCLARECIDO\n\n"
    "Declaro que fui informado(a) sobre os objetivos, procedimentos e natureza do atendimento "
    "psicológico realizado por {professional}, profissional "
    "regularmente inscrito(a) e atuando conforme as normas estabelecidas pelo Conselho Federal de Psicologia "
    "e pelo Código de Ética Profissional do Psicólogo.\n\n"
    "Estou ciente de que o atendimento psicológico é pautado no respeito à dignidade e integridade humana "
    "e que todas as informações compartilhadas serão mantidas sob sigilo profissional.\n\n"
    "Compreendo que o sigilo poderá ser rompido apenas em situações previstas em lei ou diante de risco "
    "iminente à vida (do paciente ou de terceiros), bem como em casos de negligência ou abuso de incapazes.\n\n"
    "Os registros psicológicos serão armazenados de forma segura, conforme a Lei nº 13.709/2018 (LGPD).\n\n"
    "Estou ciente de que posso interromper o atendimento a qualquer momento, mediante comunicação prévia."
)


def consent_term_text(owner: User) -> str:
    professional = owner.name or "o(a) psicólogo(a) responsável"
    # Sem CRP cadastrado, o termo avisa explicitamente em vez de inventar um número.
    professional += f" – CRP {owner.crp}" if owner.crp else " (CRP não informado)"
    return CONSENT_TERM_TEMPLATE.format(professional=professional)

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
def patient_public(p: Patient, owner: User) -> dict:
    return {
        "id": p.id, 
        "full_name": p.full_name,
        "cpf": decrypt_field(p.cpf) if p.cpf else "",
        "rg": decrypt_field(p.rg) if p.rg else "",
        # Usamos .isoformat() para transformar objetos Date em string "YYYY-MM-DD" para o JSON
        "birth_date": p.birth_date.isoformat() if p.birth_date else "", 
        "age": p.age or "",
        "education": p.education or "",
        "marital_status": p.marital_status or "",
        "profession": p.profession or "",
        "phone": p.phone or "",
        "email": p.email or "", 
        "address": decrypt_field(p.address) if p.address else "",
        "emergency_contact": p.emergency_contact or "",
        "initial_notes": p.initial_notes or "",
        "consent_terms": p.consent_terms or False,
        "consent_terms_status": "Concordo" if p.consent_terms else "Não concordo",
        # Item 6: termo gerado com o nome/CRP do profissional dono do paciente.
        "consent_terms_text": consent_term_text(owner),
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
        "crp": u.crp or "",
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

async def _session_from_token(token: str, db: AsyncSession) -> Optional[UserSession]:
    """Valida a assinatura/expiração do JWT e busca a sessão correspondente no banco."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

    session_id = payload.get("sid")
    if not session_id:
        # Tokens emitidos antes da correção do item 8 não têm "sid" e não podem
        # ser revogados; por isso deixam de ser aceitos (o usuário faz login de novo).
        return None

    stmt = select(UserSession).where(
        UserSession.session_token == session_id,
        UserSession.user_id == payload.get("sub"),
    )
    sess = (await db.execute(stmt)).scalar_one_or_none()
    if not sess:
        return None  # sessão encerrada no logout (ou removida)

    expires_at = sess.expires_at
    if expires_at.tzinfo is None:  # SQLite devolve datetime sem fuso
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return sess if expires_at >= now_utc() else None


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    # CORREÇÃO DE SEGURANÇA (itens 7 e 8 da auditoria):
    # - A autenticação agora vem SOMENTE do cookie httpOnly `access_token`.
    #   O suporte a "Authorization: Bearer" foi removido junto com o token no
    #   corpo das respostas: não existe mais forma de o JavaScript obter o token.
    # - O antigo ramo "session_token" (resto do login Google anterior) foi
    #   unificado: toda sessão, seja de senha ou Google, é uma linha em
    #   `user_sessions` referenciada pelo JWT.
    token = request.cookies.get("access_token")
    sess = await _session_from_token(token, db) if token else None

    if sess:
        user = (await db.execute(select(User).where(User.user_id == sess.user_id))).scalar_one_or_none()
        if user:
            return user

    raise HTTPException(status_code=401, detail="Usuário não autenticado")
# ---------------------------------------------------------------------------

# CORREÇÃO DE SEGURANÇA (item 7 da auditoria): as rotas de autenticação
# respondiam {"user": ..., "token": "<JWT>"}. Com o token no corpo, qualquer
# script rodando na página (ex.: uma dependência npm comprometida ou um XSS)
# poderia lê-lo e enviá-lo para fora, anulando a proteção do cookie httpOnly.
# Agora a resposta traz só {"user": ...}; o token viaja apenas no cookie.
#
# CORREÇÃO DE SEGURANÇA (item 9 da auditoria): cada rota abaixo tem limite de
# requisições por IP (ver rate_limit.py). Valores pensados para não atrapalhar
# o uso real nem o desenvolvimento, mas travar automação.

@api_router.post("/auth/register", dependencies=[Depends(rate_limit("register", 5, 600))])
async def register_user(data: RegisterInput, response: Response, db: AsyncSession = Depends(get_db)):
    if not data.terms_accepted:
        raise HTTPException(status_code=400, detail="É necessário aceitar os Termos de Privacidade (LGPD).")

    email = data.email.lower().strip()

    # Verifica se já existe
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="E-mail já cadastrado.")

    # Cria o novo usuário (senha já validada pela política do item 10 em RegisterInput)
    new_user = User(
        email=email,
        name=data.name,
        password_hash=hash_password(data.password),
        auth_provider="email",
        terms_accepted=True
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user) # Atualiza o objeto com os dados gerados pelo banco (ex: user_id)

    set_auth_cookie(response, await open_login_session(db, new_user))

    await log_audit(db, new_user.user_id, email, "criar", "usuario", new_user.user_id, "Cadastro de profissional")

    return {"user": public_user(new_user)}

@api_router.post("/auth/login", dependencies=[Depends(rate_limit("login", 10, 60))])
async def login(data: LoginInput, response: Response, db: AsyncSession = Depends(get_db)):
    email = data.email.lower().strip()

    # Item 9: além do limite por IP (em `dependencies` acima), limita por CONTA.
    # Isso barra força bruta distribuída (muitos IPs tentando a mesma conta).
    enforce(f"login-email:{email}", 10, 900)

    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")

    set_auth_cookie(response, await open_login_session(db, user))

    return {"user": public_user(user)}

@api_router.post("/auth/google", dependencies=[Depends(rate_limit("google", 10, 60))])
async def google_auth(data: GoogleAuthInput, response: Response, db: AsyncSession = Depends(get_db)):
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")

    if not GOOGLE_CLIENT_ID:
        logging.critical("SECURITY ALERT: GOOGLE_CLIENT_ID não configurado.")
        raise HTTPException(status_code=500, detail="Configuração de autenticação ausente.")

    try:
        # 1. VALIDAÇÃO CRIPTOGRÁFICA OFICIAL
        # A função verifica a assinatura do JWT contra os servidores do Google.
        # O parâmetro GOOGLE_CLIENT_ID protege contra o "Confused Deputy Attack",
        # garantindo que o token foi emitido especificamente para o seu software.
        id_info = id_token.verify_oauth2_token(
            data.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )

        # 2. Validação de conta verificada
        if not id_info.get("email_verified", False):
            raise HTTPException(status_code=403, detail="Acesso negado: E-mail não verificado pelo Google.")

        email = id_info["email"].lower().strip()
        name = id_info.get("name", "")
        picture = id_info.get("picture", "")

    except ValueError as e:
        # Disparado se o token for inválido, estiver expirado ou o Audience (Client ID) não bater.
        logging.warning(f"Tentativa de falsificação de token Google: {str(e)}")
        raise HTTPException(status_code=401, detail="Token de autenticação inválido ou expirado.")

    # 3. Lógica de Persistência no Banco (Segura)
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user:
        # Item 6: o nome agora pode ser editado em Configurações (vai no termo de
        # consentimento). O Google só preenche o nome se ele ainda estiver vazio,
        # para não sobrescrever a edição do profissional a cada login.
        user.name = user.name or name
        user.picture = picture
    else:
        # Nota de Segurança: O ideal em sistemas médicos é que a conta seja
        # pré-aprovada ou que exista um fluxo explícito de aceite dos termos da LGPD
        user = User(
            email=email,
            name=name,
            picture=picture,
            auth_provider="google",
            terms_accepted=True
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)

    # 4. Geração da NOSSA sessão. Nunca dependemos da sessão do Google a longo prazo.
    set_auth_cookie(response, await open_login_session(db, user))

    return {"user": public_user(user)}

@api_router.get("/auth/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.webhook_token:
        user.webhook_token = f"wh_{uuid.uuid4().hex}"
        await db.commit()
        await db.refresh(user)

    return public_user(user)

@api_router.put("/auth/profile")
async def update_profile(data: ProfileInput, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Item 6: nome e CRP do profissional, usados no termo de consentimento dos pacientes.
    user.name = data.name
    user.crp = data.crp
    await db.commit()
    await db.refresh(user)

    await log_audit(db, user.user_id, user.email, "editar", "usuario", user.user_id, "Dados profissionais (nome/CRP)")
    return public_user(user)

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    # Item 8: apaga a sessão no banco, o que invalida o JWT imediatamente,
    # mesmo que alguém tenha copiado o token antes.
    token = request.cookies.get("access_token")
    sess = await _session_from_token(token, db) if token else None
    if sess:
        await db.delete(sess)
        await db.commit()

    # Os atributos precisam bater com os do set_cookie, senão alguns
    # navegadores não removem o cookie `secure` em produção.
    is_prod = ENVIRONMENT == "production"
    response.delete_cookie("access_token", path="/", httponly=True,
                           samesite="strict" if is_prod else "lax", secure=is_prod)
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
    return [patient_public(p, user) for p in docs]

@api_router.get("/patients/{pid}")
async def get_patient(pid: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Busca o paciente pelo ID, garantindo que pertence ao usuário logado
    stmt = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    result = await db.execute(stmt)
    p = result.scalar_one_or_none()
    
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
        
    return patient_public(p, user)

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
        marital_status=data.marital_status,
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
    return patient_public(novo_paciente, user)

@api_router.put("/patients/{pid}")
async def update_patient(pid: str, data: PatientInput, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    p = (await db.execute(stmt)).scalar_one_or_none()

    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    p.full_name = data.full_name.strip()
    p.cpf = encrypt_field(data.cpf)
    p.rg = encrypt_field(data.rg)
    p.birth_date = data.birth_date
    p.age = data.age
    p.education = data.education
    p.marital_status = data.marital_status
    p.profession = data.profession
    p.phone = data.phone
    p.email = data.email
    p.address = encrypt_field(data.address)
    p.emergency_contact = data.emergency_contact
    p.initial_notes = data.initial_notes
    p.consent_terms = data.consent_terms
    p.updated_at = now_utc()

    await db.commit()
    await db.refresh(p)

    await log_audit(db, user.user_id, user.email, "editar", "paciente", p.id, f"Paciente {data.full_name}")
    return patient_public(p, user)

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
                    
    return patient_public(p, user)

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
    
    pat = patient_public(p, user)
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
    
    # CORREÇÃO DE SEGURANÇA (item 4 da auditoria): injeção de marcação no PDF.
    # O Paragraph do ReportLab interpreta uma mini-linguagem parecida com HTML
    # (<b>, <font>, <img src=...>, <a href=...>). Antes, nome, CPF, diagnóstico e
    # evolução clínica entravam CRUS nessa marcação. Consequências:
    #   - um texto comum com "<" ou "&" (ex.: "ansiedade < 3 meses") quebrava
    #     a exportação com erro 500;
    #   - uma tag <img src="/caminho/no/servidor"> ou <img src="http://...">
    #     fazia o SERVIDOR ler arquivos locais ou acessar URLs arbitrárias.
    # Agora TODO valor vindo do usuário passa por pdf_text(), que escapa & < >
    # e só depois converte as quebras de linha em <br/>. A marcação fixa do
    # layout (<b>...</b>) continua funcionando porque é escrita por nós.
    def pdf_text(value) -> str:
        return escape(str(value or "")).replace("\n", "<br/>")

    elems = [Paragraph("Prontuário Clínico — PsiGestão", h), Spacer(1, 4)]
    elems.append(Paragraph(f"Profissional: {pdf_text(user.name)} · {pdf_text(user.email)}", meta))
    elems.append(Paragraph(f"Emitido em: {now_utc().strftime('%d/%m/%Y %H:%M UTC')}", meta))
    elems.append(Spacer(1, 8))
    elems.append(HRFlowable(width="100%", color=navy))
    elems.append(Spacer(1, 8))
    elems.append(Paragraph("Dados do Paciente", sub))
    elems.append(Paragraph(f"<b>Nome:</b> {pdf_text(pat['full_name'])}", body))
    elems.append(Paragraph(f"<b>CPF:</b> {pdf_text(pat['cpf'] or '—')}", body))
    elems.append(Paragraph(f"<b>Nascimento:</b> {pdf_text(pat['birth_date'] or '—')}", body))
    elems.append(Paragraph(f"<b>Telefone:</b> {pdf_text(pat['phone'] or '—')}", body))
    elems.append(Paragraph(f"<b>E-mail:</b> {pdf_text(pat['email'] or '—')}", body))
    elems.append(Paragraph(f"<b>Termo de consentimento:</b> {pdf_text(pat['consent_terms_status'])}", body))
    elems.append(Paragraph(pdf_text(pat["consent_terms_text"]), body))
    elems.append(Spacer(1, 10))
    elems.append(Paragraph("Evolução Clínica", sub))
    if not recs:
        elems.append(Paragraph("Nenhum prontuário registrado.", body))
    for r in recs:
        elems.append(Spacer(1, 6))
        elems.append(Paragraph(f"<b>Sessão:</b> {pdf_text(r['session_datetime'])}  (v{r['version']})", meta))
        if r["diagnosis"]:
            elems.append(Paragraph(f"<b>Diagnóstico:</b> {pdf_text(r['diagnosis'])}", body))
        elems.append(Paragraph(pdf_text(r["content"]), body))
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
# (Header já importado no topo do arquivo)

class WebhookPayload(BaseModel):
    patient_data: PatientInput

# Item 9: o token do webhook é o único segredo desta rota pública. O limite
# impede tanto adivinhar tokens quanto inundar a base com pacientes falsos
# caso o token vaze. 30/min comporta com folga um formulário real.
@api_router.post("/webhook/google-forms", dependencies=[Depends(rate_limit("webhook", 30, 60))])
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
        marital_status=data.marital_status,
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