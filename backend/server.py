import asyncio
import base64
import hmac
import io
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional
from fastapi import Response

import bcrypt
import google.auth.transport
from google.auth import exceptions as google_exceptions
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import jwt
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import delete, desc, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from starlette.concurrency import run_in_threadpool
from xml.sax.saxutils import escape

import legal
import observability
from crypto import (
    hash_token, is_fernet_token, load_fernet, load_search_key, name_search_tokens, query_search_tokens,
)
from rate_limit import client_ip, enforce, rate_limit, warn_if_not_shared
from database import get_db, engine, Base
from environment import get_environment
from models import (
    AuditLog, Patient, PatientSearchToken, Record, RecordVersion, Session, User, UserSession, now_utc,
)

# MONITORAMENTO (escala): configura os logs antes de qualquer outra coisa,
# para que até os alertas de inicialização abaixo saiam no formato certo
# (JSON em produção) e com o ID da requisição. Detalhes em observability.py.
observability.configure_logging()

# -----------------------------------------------------------
# Configurações Iniciais (Mantenha as suas variáveis de ambiente aqui)
# -----------------------------------------------------------
# -----------------------------------------------------------
# Configurações Iniciais e Validação de Segurança
# -----------------------------------------------------------
# SEGURANÇA (varredura, item 5): o padrão agora é PRODUÇÃO. Antes, esquecer
# ENVIRONMENT no deploy fazia o servidor subir calado com os valores
# inseguros de desenvolvimento (segredos de exemplo, cookie sem Secure,
# /docs aberto). Agora só um ENVIRONMENT=development explícito (docker-compose.yml)
# liga o modo de desenvolvimento. Detalhes em environment.py.
ENVIRONMENT = get_environment()

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

# ESCALA (paginação/busca): chave do índice cego de busca por nome, separada
# da FERNET_KEY. Obrigatória em produção. Detalhes em crypto.py.
_search_key = load_search_key(ENVIRONMENT)


def set_auth_cookie(response: Response, token: str) -> None:
    """
    Configura o cookie de autenticação com restrições baseadas no ambiente.

    CORREÇÃO DE SEGURANÇA (item 1 da auditoria): esta função era sobrescrita por
    uma cópia mais abaixo no arquivo que fixava secure=False e samesite="lax".
    A cópia foi removida, então esta é a versão que vale de fato.

    ATENÇÃO: em produção (o padrão quando ENVIRONMENT não é "development"; ver
    environment.py) o cookie é `secure`, ou seja, o navegador só o envia por
    HTTPS. Em produção o app PRECISA estar atrás de HTTPS.
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

def api_docs_urls(environment: str) -> dict:
    """SEGURANÇA (varredura, item 12): a documentação automática do FastAPI
    (/docs, /redoc e /openapi.json) entregava a qualquer visitante o mapa
    completo da API: todas as rotas, campos e formatos aceitos. Ela continua
    disponível no desenvolvimento e é desligada em produção (as três rotas
    passam a responder 404)."""
    if environment == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


# 1. Inicializa o app UMA ÚNICA VEZ
app = FastAPI(title="PsiGestão API", **api_docs_urls(ENVIRONMENT))

# ESCALA (workers): quem cria/migra o banco.
# Antes, TODO processo rodava create_all() ao iniciar. Com vários workers,
# todos fariam isso ao mesmo tempo (corrida em DDL) e o banco ainda seria
# alterado fora do controle do Alembic. Agora:
#   - produção: o prestart.py roda UMA vez, antes de os workers subirem,
#     com trava no banco, e aplica as migrações (ver start.sh);
#   - desenvolvimento: continua criando as tabelas sozinho, como antes.
AUTO_CREATE_TABLES = os.environ.get(
    "AUTO_CREATE_TABLES", "false" if ENVIRONMENT == "production" else "true"
).lower() == "true"


@app.on_event("startup")
async def init_tables():
    if not AUTO_CREATE_TABLES:
        return
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
    # Monitoramento: o navegador só deixa o frontend ler o ID da requisição
    # (para mostrar ao usuário ao reportar um erro) se ele for exposto aqui.
    expose_headers=["X-Request-ID"],
)

# CORREÇÃO DE CONFORMIDADE (item 14 da auditoria): origem de cada ação.
# A trilha de auditoria não registrava de ONDE vinha a ação. Este middleware
# guarda o IP e o navegador da requisição atual em variáveis de contexto,
# que o log_audit() lê. Assim nenhuma rota precisa repassar o `request` só
# para auditar, e nenhuma rota nova esquece de registrar o IP.
# Atrás de proxy, o IP real depende de `--proxy-headers` no uvicorn (ver rate_limit.py).
_audit_ip: ContextVar[str] = ContextVar("audit_ip", default="")
_audit_user_agent: ContextVar[str] = ContextVar("audit_user_agent", default="")


class AuditContextMiddleware:
    """ESCALA: antes era um @app.middleware("http") (BaseHTTPMiddleware), que
    cria uma tarefa extra por requisição. Como middleware ASGI puro, faz o
    mesmo trabalho (guardar IP e navegador para a auditoria) sem esse custo."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            _audit_ip.set(client_ip(request))
            _audit_user_agent.set(request.headers.get("user-agent", "")[:300])
        await self.app(scope, receive, send)


app.add_middleware(AuditContextMiddleware)
# Adicionado por último = camada mais externa: mede o tempo de TODA a
# requisição (inclusive CORS) e garante o X-Request-ID em todas as respostas.
app.add_middleware(observability.RequestContextMiddleware)

# Monitoramento: Sentry (só com SENTRY_DSN) e alerta de rate limit não
# compartilhado entre workers (ver rate_limit.py).
observability.init_sentry(ENVIRONMENT)
warn_if_not_shared(ENVIRONMENT)

# Aviso de conformidade: dados do controlador/encarregado em branco na política.
legal.warn_if_incomplete(ENVIRONMENT)

# 3. Cria e inclui as suas rotas
api_router = APIRouter(prefix="/api")


# 4. Configuração de logs (formato e destino ficam em observability.configure_logging)
logger = logging.getLogger("psigestao")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# ESCALA (event loop): o bcrypt é LENTO DE PROPÓSITO (~250 ms de CPU por
# chamada, para dificultar força bruta). Chamado direto numa rota `async`, ele
# congelava o event loop: durante cada login, NENHUMA outra requisição daquele
# worker era atendida. Agora roda numa thread (a lib libera o GIL durante o
# cálculo), e o loop segue atendendo as demais requisições.
async def hash_password_async(password: str) -> str:
    return await run_in_threadpool(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await run_in_threadpool(verify_password, password, password_hash)


# Hash bcrypt de uma senha descartável, usado quando o e-mail não existe.
# Sem ele, o login respondia ~250 ms mais rápido para e-mails inexistentes,
# e essa diferença permitia descobrir quais e-mails têm conta.
_DUMMY_PASSWORD_HASH = "$2b$12$XB8K8.SvfzFWF1bUeVOQX.NidjFnFzv5OIXghYUeKyKF/F/P5C4Tm"


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
    # ESCALA: cada login cria uma linha, e sessões que expiram sem logout
    # ficavam para sempre, fazendo a tabela crescer sem limite. A cada login
    # apagamos as vencidas DESTE usuário (consulta curta, pelo índice user_id).
    await db.execute(delete(UserSession).where(
        UserSession.user_id == user.user_id, UserSession.expires_at < now_utc(),
    ))

    expires_at = now_utc() + timedelta(hours=JWT_EXPIRE_HOURS)
    session_id = secrets.token_urlsafe(32)
    db.add(UserSession(session_token=session_id, user_id=user.user_id, expires_at=expires_at,
                       last_activity_at=now_utc()))  # produto: ponto de partida da inatividade
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
#
# TERMO DE CONSENTIMENTO POR PROFISSIONAL (etapa de conformidade):
# O termo tem duas partes:
#   1. Cabeçalho fixo, gerado pelo sistema: título + identificação do
#      profissional (nome e CRP). Não pode ser removido pelo profissional,
#      para que todo termo diga claramente quem é o responsável.
#   2. Corpo: o modelo padrão abaixo OU um texto próprio do profissional
#      (Configurações -> Termo de consentimento), para incluir cláusulas como
#      atendimento online, faltas ou gravação.
#
# PRODUTO (primeira versão): identificação do profissional DESLIGADA.
# Nesta versão o sistema é usado só pelos profissionais; os pacientes ainda
# não têm acesso. Por isso o nome e o CRP do profissional saem do termo de
# consentimento e do PDF exportado, e o cadastro de nome/CRP some das
# Configurações. Nada foi apagado: quando o sistema for aberto aos pacientes,
# basta definir SHOW_PROFESSIONAL_IDENTIFICATION=true para tudo voltar
# (inclusive os nomes e CRPs já cadastrados), sem mudar código.
# Consentimentos já registrados NÃO mudam: cada paciente guarda a cópia do
# texto que aceitou (ver apply_consent).
SHOW_PROFESSIONAL_IDENTIFICATION = os.environ.get("SHOW_PROFESSIONAL_IDENTIFICATION", "false").lower() == "true"

# O corpo padrão não depende do cabeçalho: antes dizia "profissional acima
# identificado(a)", o que ficaria sem sentido com a identificação desligada.
DEFAULT_CONSENT_TERM_BODY = (
    "Declaro que fui informado(a) sobre os objetivos, procedimentos e natureza do atendimento "
    "psicológico realizado pelo(a) psicólogo(a) responsável pelo meu atendimento, regularmente "
    "inscrito(a) no Conselho Regional de Psicologia e atuando conforme as normas estabelecidas pelo "
    "Conselho Federal de Psicologia e pelo Código de Ética Profissional do Psicólogo.\n\n"
    "Estou ciente de que o atendimento psicológico é pautado no respeito à dignidade e integridade humana "
    "e que todas as informações compartilhadas serão mantidas sob sigilo profissional.\n\n"
    "Compreendo que o sigilo poderá ser rompido apenas em situações previstas em lei ou diante de risco "
    "iminente à vida (do paciente ou de terceiros), bem como em casos de negligência ou abuso de incapazes.\n\n"
    "Os registros psicológicos serão armazenados de forma segura, conforme a Lei nº 13.709/2018 (LGPD).\n\n"
    "Estou ciente de que posso interromper o atendimento a qualquer momento, mediante comunicação prévia."
)
CONSENT_TERM_MAX_LENGTH = 10_000


def consent_term_body(owner: User) -> str:
    return (owner.consent_term_body or "").strip() or DEFAULT_CONSENT_TERM_BODY


def consent_term_text(owner: User) -> str:
    """Texto ATUAL do termo deste profissional (usado para novos consentimentos)."""
    header = "TERMO DE CONSENTIMENTO LIVRE E ESCLARECIDO\n\n"
    if SHOW_PROFESSIONAL_IDENTIFICATION:  # desligado na primeira versão (ver acima)
        professional = owner.name or "o(a) psicólogo(a) responsável"
        # Sem CRP cadastrado, o termo avisa explicitamente em vez de inventar um número.
        professional += f" – CRP {owner.crp}" if owner.crp else " (CRP não informado)"
        header += f"Profissional responsável: {professional}\n\n"
    return header + consent_term_body(owner)


def apply_consent(p: Patient, consent: bool, owner: User) -> Optional[str]:
    """Registra a mudança de consentimento com EVIDÊNCIA (LGPD art. 8º).

    - Não consentido -> consentido: guarda uma cópia exata do texto atual e a
      data. É essa cópia (e não o texto atual) que o paciente passa a ver:
      mudanças futuras no nome, CRP ou modelo do profissional não reescrevem
      o que já foi aceito.
    - Consentido -> revogado: registra a data da revogação (art. 8º, §5º),
      mantendo a cópia e a data originais, porque o que foi feito enquanto o
      consentimento valia continua legítimo.
    Retorna a ação para a auditoria ("consentir" / "revogar") ou None.
    """
    had_consent = bool(p.consent_terms)
    p.consent_terms = consent

    if consent and not had_consent:
        p.consent_term_snapshot = consent_term_text(owner)
        p.consent_given_at = now_utc()
        p.consent_revoked_at = None
        return "consentir"
    if had_consent and not consent:
        p.consent_revoked_at = now_utc()
        return "revogar"
    return None


class ConsentTermInput(BaseModel):
    body: str = Field(default="", max_length=CONSENT_TERM_MAX_LENGTH)


class AcceptTermsInput(BaseModel):
    # O cliente informa QUAL versão leu. Se a política mudou enquanto a tela
    # estava aberta, o aceite é recusado em vez de valer para um texto não lido.
    version: str

# CORREÇÃO DE PRODUTO (horários deslocados):
# O frontend mandava a data do campo "datetime-local" SEM fuso horário
# ("2026-10-02T09:00"). O servidor a gravava como se fosse UTC, e a pessoa
# no Brasil (UTC-3) via a sessão das 09:00 aparecer às 06:00, inclusive na
# data das evoluções do prontuário. Agora o servidor RECUSA data sem fuso:
# quem chama precisa dizer qual instante quer (o frontend envia em UTC).
def _require_timezone(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Data e hora sem fuso horário. Envie em ISO 8601 com fuso (ex.: 2026-10-02T09:00:00-03:00).")
    return value.astimezone(timezone.utc)


class RecordInput(BaseModel):
    session_datetime: datetime # Tipagem alterada para datetime nativo
    content: str
    diagnosis: Optional[str] = ""

    @field_validator("session_datetime")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value)


# PRODUTO (visões da agenda): regras que o servidor passa a garantir.
SESSION_STATUSES = ("agendada", "realizada", "cancelada")
# Duração padrão de uma sessão de psicoterapia, usada quando não há horário de
# término (inclusive na detecção de conflito). O frontend usa o mesmo valor.
DEFAULT_SESSION_MINUTES = 50
MAX_SESSION_DURATION = timedelta(hours=12)


class SessionInput(BaseModel):
    patient_id: Optional[str] = ""
    patient_name: Optional[str] = ""
    title: str = Field(max_length=200)
    start: datetime # Tipagem alterada para datetime nativo
    end: Optional[datetime] = None # Tipagem alterada para datetime nativo
    # Antes aceitava qualquer texto (item 17 da auditoria); a agenda só conhece estes três.
    status: Literal["agendada", "realizada", "cancelada"] = "agendada"
    notes: Optional[str] = Field(default="", max_length=5000)
    # Conflito de horário: o servidor recusa (409) uma sessão que se sobrepõe a
    # outra, a menos que o profissional confirme ("salvar mesmo assim").
    allow_conflict: bool = False

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Informe um título para a sessão.")
        return value

    @field_validator("start", "end")
    @classmethod
    def validate_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _require_timezone(value)

    @model_validator(mode="after")
    def validate_period(self):
        # Antes era possível gravar uma sessão que terminava antes de começar.
        if self.end is not None:
            if self.end <= self.start:
                raise ValueError("O horário de término deve ser depois do início.")
            if self.end - self.start > MAX_SESSION_DURATION:
                raise ValueError("Uma sessão não pode durar mais de 12 horas.")
        return self


# PRODUTO (bloqueio por inatividade)
LOCK_MINUTES_OPTIONS = (5, 10, 15, 30)
DEFAULT_LOCK_MINUTES = 10


class LockInput(BaseModel):
    # "idle" = o navegador detectou inatividade; "manual" = botão "Bloquear agora".
    reason: Literal["idle", "manual"] = "manual"


class UnlockInput(BaseModel):
    # Quem tem senha desbloqueia com a senha; quem entra pelo Google, com o Google.
    password: Optional[str] = Field(default=None, max_length=256)
    credential: Optional[str] = Field(default=None, max_length=8192)


class LockSettingsInput(BaseModel):
    minutes: int

    @field_validator("minutes")
    @classmethod
    def validate_minutes(cls, value: int) -> int:
        if value not in LOCK_MINUTES_OPTIONS:
            raise ValueError("Escolha 5, 10, 15 ou 30 minutos.")
        return value


# ---------------------------------------------------------------------------
# Funções Auxiliares de Serialização (Pacientes e Prontuários)
# ---------------------------------------------------------------------------
def _iso(value) -> str:
    return value.isoformat() if value else ""


def patient_public(p: Patient, owner: User) -> dict:
    # Item 13: nome, telefone, e-mail, contato de emergência e observações
    # agora também são descriptografados aqui (antes ficavam em texto puro).
    return {
        "id": p.id,
        "full_name": decrypt_field(p.full_name),
        "cpf": decrypt_field(p.cpf),
        "rg": decrypt_field(p.rg),
        # Usamos .isoformat() para transformar objetos Date em string "YYYY-MM-DD" para o JSON
        "birth_date": p.birth_date.isoformat() if p.birth_date else "",
        "age": p.age or "",
        "education": p.education or "",
        "marital_status": p.marital_status or "",
        "profession": p.profession or "",
        "phone": decrypt_field(p.phone),
        "email": decrypt_field(p.email),
        "address": decrypt_field(p.address),
        "emergency_contact": decrypt_field(p.emergency_contact),
        "initial_notes": decrypt_field(p.initial_notes),
        "consent_terms": p.consent_terms or False,
        "consent_terms_status": "Concordo" if p.consent_terms else "Não concordo",
        # Termo por profissional: se o paciente consentiu, mostramos a CÓPIA do
        # texto que ele aceitou; senão, o texto atual do profissional (item 6).
        "consent_terms_text": (
            p.consent_term_snapshot if p.consent_terms and p.consent_term_snapshot
            else consent_term_text(owner)
        ),
        "consent_given_at": _iso(p.consent_given_at),
        "consent_revoked_at": _iso(p.consent_revoked_at),
        "last_consultation_date": p.last_consultation_date or "",
        "anonymized": p.anonymized or False,
        # Item 12: paciente arquivado (fora da lista ativa, dados preservados).
        "archived": p.archived_at is not None,
        "archived_at": _iso(p.archived_at),
        # Trata o DateTime com timezone para retornar string ISO completa
        "created_at": _iso(p.created_at),
    }


def _apply_patient_input(p: Patient, data: PatientInput) -> None:
    """Copia o formulário para o paciente, criptografando os campos sensíveis.

    Antes, criar, editar e o webhook repetiam esta lista cada um à sua maneira,
    e bastava esquecer um encrypt_field() em um deles para gravar dado em claro.
    Agora os três usam esta função. (O consentimento é tratado à parte, em
    apply_consent, porque precisa registrar evidência.)
    """
    p.full_name = encrypt_field(data.full_name.strip())
    p.cpf = encrypt_field(data.cpf)
    p.rg = encrypt_field(data.rg)
    p.birth_date = data.birth_date
    p.age = data.age
    p.education = data.education
    p.marital_status = data.marital_status
    p.profession = data.profession
    p.phone = encrypt_field(data.phone)
    p.email = encrypt_field(data.email)
    p.address = encrypt_field(data.address)
    p.emergency_contact = encrypt_field(data.emergency_contact)
    p.initial_notes = encrypt_field(data.initial_notes)

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

def has_accepted_current_terms(u: User) -> bool:
    # Item 16: vale o aceite da versão VIGENTE da política, não um booleano
    # antigo. Contas que aceitaram antes de existir a política versionada
    # (ou antes de uma mudança de versão) precisam aceitar de novo.
    return bool(u.terms_accepted) and u.terms_version == legal.PRIVACY_POLICY_VERSION


def public_user(u: User) -> dict:
    return {
        "user_id": u.user_id,
        "email": u.email,
        "name": u.name or "",
        "picture": u.picture or "",
        "auth_provider": u.auth_provider or "email",
        "crp": u.crp or "",
        "terms_accepted": has_accepted_current_terms(u),
        "terms_version": u.terms_version or "",
        "terms_accepted_at": _iso(u.terms_accepted_at),
        "privacy_policy_version": legal.PRIVACY_POLICY_VERSION,
        # Item 15: o token do webhook NÃO é mais devolvido aqui. Antes ia em
        # toda resposta de /auth/me, ou seja, em toda carga de página. Agora ele
        # só aparece uma vez, ao ser gerado (POST /integrations/google-forms/token).
        "webhook_configured": bool(u.webhook_token_hash),
        # Produto: tempo do bloqueio por inatividade e como desbloquear a tela
        # (senha para quem tem; Google para quem só entra pelo Google).
        "lock_minutes": u.lock_minutes or DEFAULT_LOCK_MINUTES,
        "has_password": bool(u.password_hash),
        # Recursos ligados/desligados nesta implantação (o frontend esconde o
        # que estiver desligado, ex.: o cadastro de nome/CRP nas Configurações).
        "features": {"professional_identification": SHOW_PROFESSIONAL_IDENTIFICATION},
    }

# Refatoração da trilha de auditoria para usar o SQLAlchemy
async def log_audit(db: AsyncSession, owner_id: str, user_email: str, action: str, entity_type: str,
                    entity_id: str, detail: str = ""):
    # REGRAS DA TRILHA (itens 13 e 14 da auditoria):
    # - `detail` NUNCA leva nome, contato ou conteúdo do paciente: esta tabela
    #   não é criptografada. Quem é o paciente se descobre pelo entity_id.
    # - IP e navegador vêm do middleware capture_audit_context.
    # - A tabela é somente de inclusão (gatilho no banco, ver models.py).
    novo_log = AuditLog(
        owner_id=owner_id,
        user_email=user_email,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
        ip_address=_audit_ip.get(),
        user_agent=_audit_user_agent.get(),
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

    # ESCALA: esta função roda em TODA requisição autenticada. Antes eram duas
    # idas ao banco (sessão, depois usuário); o joinedload traz os dois juntos.
    stmt = select(UserSession).options(joinedload(UserSession.user)).where(
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


async def _authenticated_session(request: Request, db: AsyncSession) -> UserSession:
    """Sessão válida do cookie, SEM checar o bloqueio por inatividade.

    CORREÇÃO DE SEGURANÇA (itens 7 e 8 da auditoria):
    - A autenticação agora vem SOMENTE do cookie httpOnly `access_token`.
      O suporte a "Authorization: Bearer" foi removido junto com o token no
      corpo das respostas: não existe mais forma de o JavaScript obter o token.
    - O antigo ramo "session_token" (resto do login Google anterior) foi
      unificado: toda sessão, seja de senha ou Google, é uma linha em
      `user_sessions` referenciada pelo JWT.
    """
    token = request.cookies.get("access_token")
    sess = await _session_from_token(token, db) if token else None
    if sess and sess.user:  # usuário já carregado junto com a sessão (joinedload)
        return sess
    raise HTTPException(status_code=401, detail="Usuário não autenticado")


# ---------------------------------------------------------------------------
# PRODUTO: bloqueio por inatividade, controlado NO SERVIDOR
# ---------------------------------------------------------------------------
# Em consultório o computador fica aberto na frente do paciente ou da
# recepção. Depois de N minutos sem uso (Configurações: 5, 10, 15 ou 30),
# a tela trava e só volta com a senha (ou o Google, para quem entra por ele).
#
# Por que no servidor e não só na tela: um bloqueio só visual cai com um F5
# ou com uma chamada direta à API. Aqui a SESSÃO fica bloqueada, e todas as
# rotas de dados respondem 423 até o desbloqueio. O navegador avisa a
# inatividade (POST /auth/lock) e manda um "sinal de vida" enquanto a pessoa
# usa a tela (POST /auth/activity); mesmo que o navegador não avise (notebook
# que hibernou, aba congelada), o servidor bloqueia sozinho quando passa do
# tempo, com 1 minuto de folga para o atraso do sinal de vida.
LOCK_GRACE = timedelta(minutes=1)
# Atualiza last_activity_at no máximo 1x por minuto: sem isso, TODA requisição
# viraria uma escrita no banco.
ACTIVITY_WRITE_INTERVAL = timedelta(seconds=60)
MAX_UNLOCK_FAILURES = 5
SESSION_LOCKED_DETAIL = {
    "code": "session_locked",
    "msg": "Sessão bloqueada por inatividade. Desbloqueie para continuar.",
}


def _session_is_idle(sess: UserSession) -> bool:
    last_activity = _as_utc(sess.last_activity_at or sess.created_at) or now_utc()
    limit = timedelta(minutes=sess.user.lock_minutes or DEFAULT_LOCK_MINUTES) + LOCK_GRACE
    return now_utc() - last_activity > limit


async def _is_locked(db: AsyncSession, sess: UserSession) -> bool:
    """True se a sessão está bloqueada. Bloqueia (e audita) se passou do tempo."""
    if sess.locked_at is not None:
        return True
    if _session_is_idle(sess):
        sess.locked_at = now_utc()
        await db.commit()
        await log_audit(db, sess.user.user_id, sess.user.email, "bloquear", "sessao", sess.user.user_id,
                        "Bloqueio por inatividade (detectado no servidor)")
        return True
    return False


async def _touch_activity(db: AsyncSession, sess: UserSession) -> None:
    now = now_utc()
    last_activity = _as_utc(sess.last_activity_at)
    if last_activity is None or now - last_activity > ACTIVITY_WRITE_INTERVAL:
        sess.last_activity_at = now
        await db.commit()


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Usuário autenticado com a sessão DESBLOQUEADA (todas as rotas de dados).

    Sessão bloqueada responde 423 com code "session_locked": o frontend
    mostra a tela de bloqueio sem perder o que estava aberto (ex.: rascunho).
    """
    sess = await _authenticated_session(request, db)
    if await _is_locked(db, sess):
        raise HTTPException(status_code=423, detail=SESSION_LOCKED_DETAIL)
    await _touch_activity(db, sess)
    return sess.user


TERMS_REQUIRED_DETAIL = "É necessário aceitar a versão atual da Política de Privacidade para continuar."


async def get_active_user(user: User = Depends(get_current_user)) -> User:
    """Usuário autenticado E com a Política de Privacidade vigente aceita.

    CORREÇÃO DE CONFORMIDADE (item 16 e política de privacidade): todas as
    rotas que tocam dados de pacientes usam esta dependência. A tela de aceite
    do frontend já impede o uso sem aceite; esta checagem no servidor garante
    a regra mesmo para quem chamar a API diretamente.
    Ficam FORA desta exigência apenas: /auth/me (o frontend precisa saber se
    deve mostrar a tela de aceite), /auth/accept-terms, /auth/logout e a
    leitura pública da política.
    """
    if not has_accepted_current_terms(user):
        # `code` permite ao frontend reconhecer este caso (e reabrir a tela de
        # aceite) sem depender do texto da mensagem; `msg` é o que o usuário lê.
        raise HTTPException(status_code=403, detail={"code": "terms_required", "msg": TERMS_REQUIRED_DETAIL})
    return user
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
        password_hash=await hash_password_async(data.password),  # escala: fora do event loop
        auth_provider="email",
        # Item 16: o aceite marcado no formulário vale para a versão vigente
        # da política (a página de cadastro linka exatamente este texto).
        terms_accepted=True,
        terms_version=legal.PRIVACY_POLICY_VERSION,
        terms_accepted_at=now_utc(),
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user) # Atualiza o objeto com os dados gerados pelo banco (ex: user_id)

    set_auth_cookie(response, await open_login_session(db, new_user))

    await log_audit(db, new_user.user_id, email, "criar", "usuario", new_user.user_id, "Cadastro de profissional")
    # Evidência do aceite: versão aceita + data/IP (gravados pelo log_audit).
    await log_audit(db, new_user.user_id, email, "aceitar", "termos", new_user.user_id,
                    f"Política de Privacidade versão {legal.PRIVACY_POLICY_VERSION}")

    return {"user": public_user(new_user)}

@api_router.post("/auth/login", dependencies=[Depends(rate_limit("login", 10, 60))])
async def login(data: LoginInput, response: Response, db: AsyncSession = Depends(get_db)):
    email = data.email.lower().strip()

    # Item 9: além do limite por IP (em `dependencies` acima), limita por CONTA.
    # Isso barra força bruta distribuída (muitos IPs tentando a mesma conta).
    await enforce(f"login-email:{email}", 10, 900)

    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # Escala: bcrypt em thread. Segurança: sempre roda o bcrypt (com o hash
    # descartável quando a conta não existe), para o tempo de resposta não
    # revelar se o e-mail está cadastrado.
    password_hash = user.password_hash if user and user.password_hash else _DUMMY_PASSWORD_HASH
    password_ok = await verify_password_async(data.password, password_hash)
    if not user or not user.password_hash or not password_ok:
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")

    set_auth_cookie(response, await open_login_session(db, user))

    return {"user": public_user(user)}

class CachedGoogleCertsRequest(google.auth.transport.Request):
    """Transporte HTTP do google-auth com cache dos certificados (ESCALA).

    verify_oauth2_token() baixa os certificados públicos do Google A CADA
    chamada, com uma requisição HTTP síncrona. Antes, dentro de uma rota
    `async`, isso travava o event loop do worker por ~100-300 ms em todo login
    com Google, e cada login ainda dependia da rede até o Google.

    Esta classe guarda a resposta pelo tempo que o próprio Google manda
    (Cache-Control: max-age, normalmente horas) e reaproveita a conexão HTTP.
    A verificação da assinatura continua sendo feita pela biblioteca oficial;
    só a busca dos certificados passa a ser cacheada. Quando o Google troca as
    chaves, as novas são publicadas antes do uso e o cache expira a tempo.
    """

    _DEFAULT_TTL_SECONDS = 3600

    def __init__(self) -> None:
        self._inner = google_requests.Request()  # mantém uma sessão HTTP reutilizável
        self._cache: dict = {}
        self._lock = threading.Lock()

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        if method != "GET" or body is not None:
            return self._inner(url, method=method, body=body, headers=headers, timeout=timeout, **kwargs)

        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(url)
            if cached and cached[0] > now:
                return cached[1]

        response = self._inner(url, method=method, headers=headers, timeout=timeout or 10, **kwargs)
        if response.status == 200:
            match = re.search(r"max-age=(\d+)", response.headers.get("cache-control", "") or "")
            ttl = int(match.group(1)) if match else self._DEFAULT_TTL_SECONDS
            with self._lock:
                self._cache[url] = (now + ttl, response)
        return response


_google_request = CachedGoogleCertsRequest()


async def verify_google_credential(credential: str) -> dict:
    """Valida o ID token do Google e devolve os dados da conta.

    Usada no login com Google e, no produto, no desbloqueio da tela para quem
    não tem senha (antes esta lógica existia só dentro da rota de login).
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        logging.critical("SECURITY ALERT: GOOGLE_CLIENT_ID não configurado.")
        raise HTTPException(status_code=500, detail="Configuração de autenticação ausente.")

    try:
        # 1. VALIDAÇÃO CRIPTOGRÁFICA OFICIAL
        # A função verifica a assinatura do JWT contra os servidores do Google.
        # O parâmetro GOOGLE_CLIENT_ID protege contra o "Confused Deputy Attack",
        # garantindo que o token foi emitido especificamente para o seu software.
        # ESCALA: roda numa thread (a verificação é síncrona e pode precisar
        # da rede) e usa o transporte com cache de certificados acima.
        id_info = await run_in_threadpool(id_token.verify_oauth2_token, credential, _google_request, client_id)
    except ValueError as e:
        # Disparado se o token for inválido, estiver expirado ou o Audience (Client ID) não bater.
        logging.warning(f"Tentativa de falsificação de token Google: {str(e)}")
        raise HTTPException(status_code=401, detail="Token de autenticação inválido ou expirado.")
    except google_exceptions.TransportError:
        # Monitoramento: falha de rede até o Google (antes virava um 500
        # genérico). 503 deixa claro que é temporário e de um terceiro.
        logger.exception("Não foi possível obter os certificados do Google.")
        raise HTTPException(status_code=503, detail="Login com Google temporariamente indisponível. Tente novamente.")

    # 2. Validação de conta verificada
    if not id_info.get("email_verified", False):
        raise HTTPException(status_code=403, detail="Acesso negado: E-mail não verificado pelo Google.")
    return id_info


@api_router.post("/auth/google", dependencies=[Depends(rate_limit("google", 10, 60))])
async def google_auth(data: GoogleAuthInput, response: Response, db: AsyncSession = Depends(get_db)):
    id_info = await verify_google_credential(data.credential)  # passos 1 e 2 (ver acima)
    email = id_info["email"].lower().strip()
    name = id_info.get("name", "")
    picture = id_info.get("picture", "")

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
        # CORREÇÃO DE CONFORMIDADE (item 16 da auditoria):
        # Antes a conta criada pelo Google nascia com terms_accepted=True, sem
        # o usuário ter visto termo nenhum: um "aceite" que não aconteceu não
        # serve como evidência e não vale perante a LGPD. Agora a conta nasce
        # SEM aceite; o frontend mostra a Política de Privacidade e só libera o
        # sistema depois de POST /auth/accept-terms (registrado com data e IP).
        user = User(
            email=email,
            name=name,
            picture=picture,
            auth_provider="google",
            terms_accepted=False,
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)

    # 4. Geração da NOSSA sessão. Nunca dependemos da sessão do Google a longo prazo.
    set_auth_cookie(response, await open_login_session(db, user))

    return {"user": public_user(user)}

@api_router.get("/auth/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    # Item 15: antes esta rota criava um token de webhook para TODO usuário e o
    # devolvia a cada carregamento de página. Agora o token só existe se o
    # profissional gerar um (rota /integrations/google-forms/token).
    #
    # Produto: responde MESMO com a sessão bloqueada (informando `locked`),
    # para o frontend mostrar a tela de bloqueio. Recarregar a página não
    # desbloqueia nada: os dados continuam recusados até o desbloqueio.
    sess = await _authenticated_session(request, db)
    locked = await _is_locked(db, sess)
    if not locked:
        await _touch_activity(db, sess)  # abrir/recarregar a página é uso real
    return {**public_user(sess.user), "locked": locked}

@api_router.post("/auth/accept-terms")
async def accept_terms(data: AcceptTermsInput, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Item 16 + política de privacidade: aceite explícito e com evidência.
    if data.version != legal.PRIVACY_POLICY_VERSION:
        raise HTTPException(
            status_code=409,
            detail="A Política de Privacidade foi atualizada enquanto a página estava aberta. Recarregue e leia a nova versão.",
        )

    user.terms_accepted = True
    user.terms_version = legal.PRIVACY_POLICY_VERSION
    user.terms_accepted_at = now_utc()
    await db.commit()
    await db.refresh(user)

    await log_audit(db, user.user_id, user.email, "aceitar", "termos", user.user_id,
                    f"Política de Privacidade versão {legal.PRIVACY_POLICY_VERSION}")
    return public_user(user)

@api_router.get("/legal/privacy-policy")
async def get_privacy_policy():
    # Pública (sem login): quem vai se cadastrar precisa poder ler a política
    # ANTES de aceitá-la. Texto e versão vêm de legal.py (fonte única).
    return legal.privacy_policy()

@api_router.put("/auth/profile")
async def update_profile(data: ProfileInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
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
# PRODUTO: rotas do bloqueio por inatividade
# ---------------------------------------------------------------------------

@api_router.post("/auth/lock")
async def lock_session(data: LockInput, request: Request, db: AsyncSession = Depends(get_db)):
    """O navegador detectou inatividade, ou a pessoa clicou em "Bloquear agora"."""
    sess = await _authenticated_session(request, db)
    if sess.locked_at is None:
        sess.locked_at = now_utc()
        await db.commit()
        detail = "Bloqueio manual" if data.reason == "manual" else "Bloqueio por inatividade"
        await log_audit(db, sess.user.user_id, sess.user.email, "bloquear", "sessao", sess.user.user_id, detail)
    return {"locked": True}


@api_router.post("/auth/unlock", dependencies=[Depends(rate_limit("unlock", 10, 60))])
async def unlock_session(data: UnlockInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Desbloqueia a sessão confirmando a identidade (senha ou Google).

    Segurança: cada erro conta. Na 5ª senha errada a sessão é ENCERRADA e a
    pessoa volta à tela de login (que tem os próprios limites). Sem isso,
    quem achasse o computador travado teria horas para testar senhas.
    """
    sess = await _authenticated_session(request, db)
    user = sess.user

    if data.password and user.password_hash:
        verified = await verify_password_async(data.password, user.password_hash)
    elif data.credential:
        id_info = await verify_google_credential(data.credential)
        # A conta do Google precisa ser A MESMA da sessão bloqueada.
        verified = id_info.get("email", "").lower().strip() == user.email
    else:
        verified = False

    if not verified:
        sess.unlock_failures = (sess.unlock_failures or 0) + 1
        if sess.unlock_failures >= MAX_UNLOCK_FAILURES:
            await db.delete(sess)
            await db.commit()
            await log_audit(db, user.user_id, user.email, "encerrar", "sessao", user.user_id,
                            "Sessão encerrada: limite de tentativas de desbloqueio")
            is_prod = ENVIRONMENT == "production"
            response.delete_cookie("access_token", path="/", httponly=True,
                                   samesite="strict" if is_prod else "lax", secure=is_prod)
            # Lançar a exceção descartaria o delete_cookie acima; por isso a
            # resposta de erro é montada aqui, já com o cookie removido.
            response.status_code = 401
            return {"detail": {"code": "session_ended",
                               "msg": "Muitas tentativas. Por segurança, a sessão foi encerrada. Entre novamente."}}
        await db.commit()
        await log_audit(db, user.user_id, user.email, "desbloquear_falha", "sessao", user.user_id,
                        f"Tentativa de desbloqueio inválida ({sess.unlock_failures}/{MAX_UNLOCK_FAILURES})")
        remaining = MAX_UNLOCK_FAILURES - sess.unlock_failures
        raise HTTPException(status_code=403, detail={
            "code": "unlock_failed",
            "msg": f"Senha incorreta. Restam {remaining} tentativa(s) antes de a sessão ser encerrada.",
            "remaining": remaining,
        })

    sess.locked_at = None
    sess.unlock_failures = 0
    sess.last_activity_at = now_utc()
    await db.commit()
    await log_audit(db, user.user_id, user.email, "desbloquear", "sessao", user.user_id, "Sessão desbloqueada")
    return {"user": {**public_user(user), "locked": False}}


@api_router.post("/auth/activity")
async def register_activity(user: User = Depends(get_current_user)):
    """"Sinal de vida": o navegador avisa que a pessoa está usando a tela
    (no máximo 1x por minuto). get_current_user já atualiza a atividade e
    responde 423 se a sessão estiver bloqueada."""
    return {"ok": True}


@api_router.put("/auth/lock-settings")
async def update_lock_settings(data: LockSettingsInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    user.lock_minutes = data.minutes
    await db.commit()
    await db.refresh(user)
    await log_audit(db, user.user_id, user.email, "editar", "usuario", user.user_id,
                    f"Bloqueio por inatividade: {data.minutes} min")
    return public_user(user)


# ---------------------------------------------------------------------------
# PRODUTO: chave dos rascunhos automáticos de prontuário
# ---------------------------------------------------------------------------
# O rascunho fica no NAVEGADOR (para sobreviver a queda de rede, sessão
# expirada, aba fechada ou travamento), mas nunca em texto puro: o navegador
# o criptografa (AES-GCM) com esta chave, que só é entregue a quem está
# logado. Um rascunho copiado do disco do computador é ilegível sem login.

@api_router.get("/auth/draft-key")
async def get_draft_key(response: Response, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    if not user.draft_key:
        user.draft_key = encrypt_field(base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
        await db.commit()
    # Nunca guardar esta resposta em cache (navegador, proxy ou CDN).
    response.headers["Cache-Control"] = "no-store"
    return {"key": decrypt_field(user.draft_key)}


@api_router.delete("/auth/draft-key")
async def rotate_draft_key(user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Troca a chave: os rascunhos de TODOS os aparelhos deixam de abrir.
    Para usar quando um computador com rascunhos é perdido ou roubado."""
    user.draft_key = None  # a próxima leitura gera uma chave nova
    await db.commit()
    await log_audit(db, user.user_id, user.email, "editar", "usuario", user.user_id,
                    "Rascunhos invalidados em todos os dispositivos")
    return {"ok": True}

# ---------------------------------------------------------------------------
# Termo de consentimento por profissional
# ---------------------------------------------------------------------------

@api_router.get("/consent-term")
async def get_consent_term(user: User = Depends(get_active_user)):
    return {
        "body": consent_term_body(user),
        "is_default": not (user.consent_term_body or "").strip(),
        "default_body": DEFAULT_CONSENT_TERM_BODY,
        # Texto completo, com o cabeçalho de identificação, como o paciente verá.
        "text": consent_term_text(user),
    }

@api_router.put("/consent-term")
async def update_consent_term(data: ConsentTermInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    body = data.body.strip()
    # Corpo vazio ou igual ao padrão = volta a acompanhar o modelo padrão
    # (assim ajustes futuros no modelo padrão chegam a quem não personalizou).
    user.consent_term_body = None if not body or body == DEFAULT_CONSENT_TERM_BODY else body
    await db.commit()
    await db.refresh(user)

    # Mudar o modelo NÃO altera consentimentos já dados: cada paciente guarda
    # a cópia do texto que aceitou (ver apply_consent).
    await log_audit(db, user.user_id, user.email, "editar", "termo", user.user_id,
                    "Modelo do termo de consentimento" + (" (padrão restaurado)" if user.consent_term_body is None else ""))
    return await get_consent_term(user)

# ---------------------------------------------------------------------------
# Integração Google Forms: token do webhook (item 15 da auditoria)
# ---------------------------------------------------------------------------

def _integration_status(u: User) -> dict:
    return {
        "configured": bool(u.webhook_token_hash),
        "hint": u.webhook_token_hint or "",
        "created_at": _iso(u.webhook_token_created_at),
    }

@api_router.get("/integrations/google-forms")
async def google_forms_status(user: User = Depends(get_active_user)):
    return _integration_status(user)

@api_router.post("/integrations/google-forms/token")
async def rotate_google_forms_token(user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Gera um token NOVO e devolve o valor uma única vez.

    - O banco guarda só o hash (crypto.hash_token); se o banco vazar, os
      tokens não servem para criar pacientes em nome de ninguém.
    - Gerar um novo token revoga o anterior na hora (o hash é substituído),
      então um token vazado se resolve em um clique.
    - O frontend precisa mostrar o script completo logo após esta chamada:
      depois disso o token não pode mais ser recuperado, só trocado.
    """
    token = f"wh_{secrets.token_urlsafe(32)}"
    user.webhook_token_hash = hash_token(token)
    user.webhook_token_hint = token[-4:]
    user.webhook_token_created_at = now_utc()
    await db.commit()
    await db.refresh(user)

    await log_audit(db, user.user_id, user.email, "gerar_token", "integracao", user.user_id,
                    "Token do Google Forms gerado (o anterior, se havia, foi revogado)")
    return {**_integration_status(user), "token": token}

@api_router.delete("/integrations/google-forms/token")
async def revoke_google_forms_token(user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    user.webhook_token_hash = None
    user.webhook_token_hint = ""
    user.webhook_token_created_at = None
    await db.commit()
    await db.refresh(user)

    await log_audit(db, user.user_id, user.email, "revogar", "integracao", user.user_id, "Token do Google Forms revogado")
    return _integration_status(user)

# ---------------------------------------------------------------------------
# Guarda obrigatória de prontuários (item 12 da auditoria)
# ---------------------------------------------------------------------------
# A Resolução CFP nº 001/2009 exige guardar o registro documental dos
# atendimentos por NO MÍNIMO 5 anos. Antes, "Excluir paciente" apagava o
# paciente e, em cascata, todos os prontuários e versões, no mesmo instante.
#
# Regra agora:
#   - paciente SEM prontuário: pode ser excluído (ex.: cadastro feito por engano);
#   - paciente COM prontuário: só pode ser excluído depois de 5 anos contados
#     da última atividade no prontuário (criação, edição ou data da sessão);
#     até lá, a opção é ARQUIVAR, que tira o paciente da lista sem apagar nada.
# A LGPD permite essa guarda mesmo contra um pedido de eliminação do titular
# (art. 16, I: cumprimento de obrigação legal ou regulatória).
#
# O prazo é o mínimo do CFP. Aumente se houver norma, contrato ou decisão
# judicial exigindo mais tempo; nunca diminua.
RECORD_RETENTION_YEARS = 5


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _add_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # 29/02 em ano não bissexto -> 28/02
        return value.replace(year=value.year + years, day=28)


async def retention_deadline(db: AsyncSession, patient_id: str) -> Optional[datetime]:
    """Data a partir da qual os prontuários do paciente podem ser eliminados.
    None = o paciente não tem prontuário (nada sob guarda)."""
    row = (await db.execute(
        select(func.max(Record.created_at), func.max(Record.updated_at), func.max(Record.session_datetime))
        .where(Record.patient_id == patient_id)
    )).one()
    moments = [_as_utc(v) for v in row if v is not None]
    if not moments:
        return None
    return _add_years(max(moments), RECORD_RETENTION_YEARS)


async def _get_owned_patient(db: AsyncSession, pid: str, user: User) -> Patient:
    stmt = select(Patient).where(Patient.id == pid, Patient.owner_id == user.user_id)
    p = (await db.execute(stmt)).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    return p

# ---------------------------------------------------------------------------
# Paginação por cursor (ESCALA)
# ---------------------------------------------------------------------------
# Antes, toda listagem devolvia TUDO de uma vez: todos os pacientes (cada um
# com o termo de consentimento completo e 8 campos descriptografados), toda a
# evolução clínica do paciente e todas as sessões da agenda desde sempre. A
# resposta e o tempo cresciam sem limite com os anos de uso.
#
# Usamos cursor (keyset) e não OFFSET: "OFFSET 5000" obriga o banco a ler e
# descartar 5000 linhas, então a página 100 fica 100x mais lenta que a 1. O
# cursor diz "continue depois de (data, id)" e o índice composto vai direto
# ao ponto: a página 1 e a 500 custam o mesmo. Também não pula nem repete
# itens quando alguém cadastra algo entre uma página e outra.
#
# Resposta: {"items": [...], "next_cursor": "..." | null}; null = última página.
# O id entra no cursor para desempatar registros com a mesma data.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def encode_cursor(moment: datetime, row_id: str) -> str:
    raw = json.dumps([_as_utc(moment).isoformat(), row_id]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        moment, row_id = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return _as_utc(datetime.fromisoformat(moment)), str(row_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Cursor de paginação inválido.")


def _split_page(rows: list, limit: int, moment_of) -> tuple:
    """Busca-se limit+1 linhas: se veio a extra, existe próxima página."""
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(moment_of(rows[-1]), _row_id(rows[-1])) if has_more and rows else None
    return rows, next_cursor


def _row_id(row) -> str:
    return getattr(row, "id", None) or getattr(row, "log_id")


# ---------------------------------------------------------------------------
# Índice cego de busca por nome (ESCALA)
# ---------------------------------------------------------------------------
async def _index_patient_name(db: AsyncSession, p: Patient, plain_name: str) -> None:
    """(Re)grava os tokens de busca do paciente. Chamado em toda criação,
    edição, anonimização e cadastro via Google Forms, na MESMA transação da
    alteração: o índice nunca fica desatualizado em relação ao nome."""
    await db.flush()  # garante o id de um paciente recém-criado
    await db.execute(delete(PatientSearchToken).where(PatientSearchToken.patient_id == p.id))
    db.add_all([
        PatientSearchToken(patient_id=p.id, owner_id=p.owner_id, token=token)
        for token in name_search_tokens(_search_key, p.owner_id, plain_name)
    ])


def patient_summary(p: Patient) -> dict:
    """Item ENXUTO da lista de pacientes (ESCALA).

    Antes a lista usava patient_public: 8 campos descriptografados e o termo
    de consentimento inteiro (~1,5 KB) POR paciente, para uma tela que mostra
    nome e duas datas. A ficha completa vem de GET /patients/{id}.
    """
    return {
        "id": p.id,
        "full_name": decrypt_field(p.full_name),
        "birth_date": p.birth_date.isoformat() if p.birth_date else "",
        "last_consultation_date": p.last_consultation_date or "",
        "anonymized": p.anonymized or False,
        "archived": p.archived_at is not None,
    }


class PatientSearchInput(BaseModel):
    q: str = Field(default="", max_length=120)
    archived: bool = False
    cursor: Optional[str] = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


async def _patients_page(db: AsyncSession, user: User, *, archived: bool, query: str,
                         cursor: Optional[str], limit: int) -> dict:
    stmt = select(Patient).where(Patient.owner_id == user.user_id)
    # Item 12: arquivados saem da lista ativa, mas continuam acessíveis por filtro.
    stmt = stmt.where(Patient.archived_at.is_not(None) if archived else Patient.archived_at.is_(None))

    # Cada palavra digitada vira um token; o paciente precisa ter TODOS.
    # Palavras de 1 letra são ignoradas (não há token para elas).
    for token in query_search_tokens(_search_key, user.user_id, query):
        stmt = stmt.where(Patient.id.in_(
            select(PatientSearchToken.patient_id).where(
                PatientSearchToken.owner_id == user.user_id, PatientSearchToken.token == token,
            )
        ))

    if cursor:
        moment, row_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Patient.created_at, Patient.id) < (moment, row_id))

    stmt = stmt.order_by(desc(Patient.created_at), desc(Patient.id)).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    rows, next_cursor = _split_page(rows, limit, lambda p: p.created_at)
    return {"items": [patient_summary(p) for p in rows], "next_cursor": next_cursor}

# ---------------------------------------------------------------------------
# Rotas de Pacientes e Prontuários
# ---------------------------------------------------------------------------

@api_router.get("/patients")
async def list_patients(
    archived: bool = Query(False, description="true = lista só os arquivados (item 12)"),
    cursor: Optional[str] = Query(None, description="next_cursor da página anterior"),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    # ESCALA: paginada e com itens enxutos (patient_summary).
    # A listagem não gera log de "visualizar" por paciente (seria um registro
    # por linha a cada abertura da tela); o acesso à ficha e ao prontuário sim.
    return await _patients_page(db, user, archived=archived, query="", cursor=cursor, limit=limit)

@api_router.post("/patients/search")
async def search_patients(data: PatientSearchInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Busca por nome, no servidor, pelo índice cego (ESCALA).

    É POST, e não GET com ?q=, DE PROPÓSITO: o termo buscado é o nome de um
    paciente. Em GET ele iria para a URL, e URLs ficam gravadas em logs de
    proxy, de load balancer, de CDN e no histórico do navegador. No corpo
    de um POST ele não é registrado por nenhuma dessas camadas.
    """
    return await _patients_page(db, user, archived=data.archived, query=data.q,
                                cursor=data.cursor, limit=data.limit)

@api_router.get("/patients/{pid}")
async def get_patient(pid: str, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    p = await _get_owned_patient(db, pid, user)
    data = patient_public(p, user)

    # Item 12: transparência sobre o prazo de guarda (mostrado na ficha).
    deadline = await retention_deadline(db, pid)
    data["retention_until"] = _iso(deadline)

    # Item 14: LEITURA também é auditada. Na LGPD, saber quem ACESSOU um dado
    # sensível importa tanto quanto saber quem o alterou. Registramos depois
    # de montar a resposta: se a leitura falhar, não houve acesso.
    await log_audit(db, user.user_id, user.email, "visualizar", "paciente", pid, "Ficha cadastral")
    return data

@api_router.post("/patients")
async def create_patient(data: PatientInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    novo_paciente = Patient(owner_id=user.user_id, anonymized=False)
    _apply_patient_input(novo_paciente, data)
    consent_action = apply_consent(novo_paciente, data.consent_terms, user)

    db.add(novo_paciente)
    await _index_patient_name(db, novo_paciente, data.full_name)  # escala: busca no servidor
    await db.commit()
    await db.refresh(novo_paciente)

    # Item 13: antes o detalhe era f"Paciente {nome}", gravando o nome em
    # texto puro na auditoria. O paciente é identificado pelo entity_id.
    await log_audit(db, user.user_id, user.email, "criar", "paciente", novo_paciente.id, "Cadastro de paciente")
    if consent_action:
        await log_audit(db, user.user_id, user.email, consent_action, "paciente", novo_paciente.id,
                        "Termo de consentimento registrado")
    return patient_public(novo_paciente, user)

@api_router.put("/patients/{pid}")
async def update_patient(pid: str, data: PatientInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    p = await _get_owned_patient(db, pid, user)

    _apply_patient_input(p, data)
    consent_action = apply_consent(p, data.consent_terms, user)
    p.updated_at = now_utc()
    await _index_patient_name(db, p, data.full_name)  # escala: o nome pode ter mudado

    await db.commit()
    await db.refresh(p)

    await log_audit(db, user.user_id, user.email, "editar", "paciente", p.id, "Edição de cadastro")
    if consent_action:
        detail = "Termo de consentimento registrado" if consent_action == "consentir" else "Consentimento revogado"
        await log_audit(db, user.user_id, user.email, consent_action, "paciente", p.id, detail)
    return patient_public(p, user)

@api_router.put("/records/{rid}")
async def update_record(rid: str, data: RecordInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
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
async def delete_patient(pid: str, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    p = await _get_owned_patient(db, pid, user)

    # Item 12: nada de prontuário é apagado dentro do prazo de guarda do CFP.
    deadline = await retention_deadline(db, pid)
    if deadline and deadline > now_utc():
        raise HTTPException(
            status_code=409,
            detail=(
                "Este paciente tem prontuários sob guarda obrigatória (Resolução CFP nº 001/2009: mínimo de "
                f"{RECORD_RETENTION_YEARS} anos após o último registro). A exclusão definitiva só será possível "
                f"a partir de {deadline:%d/%m/%Y}. Até lá, você pode arquivar o paciente."
            ),
        )

    await db.delete(p)
    await db.commit() # Sem prontuário, ou com a guarda já vencida: aí sim a exclusão é definitiva.

    detail = "Exclusão definitiva (sem prontuários)" if deadline is None else f"Exclusão definitiva (guarda encerrada em {deadline:%d/%m/%Y})"
    await log_audit(db, user.user_id, user.email, "excluir", "paciente", pid, detail)
    return {"ok": True}

@api_router.post("/patients/{pid}/archive")
async def archive_patient(pid: str, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    # Item 12: alternativa à exclusão durante a guarda. Nada é apagado nem
    # alterado; o paciente só sai da lista ativa e das contagens do painel.
    p = await _get_owned_patient(db, pid, user)
    if p.archived_at is None:
        p.archived_at = now_utc()
        await db.commit()
        await db.refresh(p)
        await log_audit(db, user.user_id, user.email, "arquivar", "paciente", pid, "Paciente arquivado")
    return patient_public(p, user)

@api_router.post("/patients/{pid}/unarchive")
async def unarchive_patient(pid: str, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    p = await _get_owned_patient(db, pid, user)
    if p.archived_at is not None:
        p.archived_at = None
        await db.commit()
        await db.refresh(p)
        await log_audit(db, user.user_id, user.email, "desarquivar", "paciente", pid, "Paciente reativado")
    return patient_public(p, user)

@api_router.post("/patients/{pid}/anonymize")
async def anonymize_patient(pid: str, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    p = await _get_owned_patient(db, pid, user)

    # Item 13: o marcador também vai criptografado, para a coluna ter um
    # formato só (token Fernet) e a rotação de chave tratar todos igual.
    p.full_name = encrypt_field("Paciente Anonimizado")
    # Escala/LGPD: os tokens do nome real saem do índice de busca junto.
    await _index_patient_name(db, p, "Paciente Anonimizado")
    p.cpf = ""
    p.rg = ""
    p.address = ""
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
async def list_records(
    pid: str,
    cursor: Optional[str] = Query(None, description="next_cursor da página anterior"),
    limit: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    # Verifica se o paciente existe e pertence ao usuário
    await _get_owned_patient(db, pid, user)

    # ESCALA: paginada (a sessão mais recente primeiro). Um paciente de anos
    # tem centenas de evoluções; antes TODAS eram descriptografadas e enviadas
    # a cada abertura da ficha. Usa o índice ix_records_patient_session.
    # session_datetime é sempre preenchido pela API (RecordInput o exige).
    base = (Record.patient_id == pid, Record.owner_id == user.user_id)
    stmt = select(Record).where(*base)
    if cursor:
        moment, row_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Record.session_datetime, Record.id) < (moment, row_id))
    stmt = stmt.order_by(desc(Record.session_datetime), desc(Record.id)).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    rows, next_cursor = _split_page(rows, limit, lambda r: r.session_datetime)
    records = [record_public(r) for r in rows]
    # Total para o contador da tela (contagem pelo índice, sem ler o conteúdo).
    total = await db.scalar(select(func.count()).select_from(Record).where(*base))

    # Item 14: acesso ao conteúdo clínico é o evento mais sensível do sistema.
    # Cada página aberta é um acesso e fica registrada.
    await log_audit(db, user.user_id, user.email, "visualizar", "prontuario", pid,
                    f"Evolução clínica ({len(records)} registro(s))")
    return {"items": records, "next_cursor": next_cursor, "total": total}

@api_router.post("/patients/{pid}/records")
async def create_record(pid: str, data: RecordInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    p = await _get_owned_patient(db, pid, user)

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

def _render_patient_pdf(pat: dict, recs: list, professional_name: str, professional_email: str,
                        consent_given_at, consent_terms: bool, consent_revoked_at) -> io.BytesIO:
    """Gera o PDF do prontuário. Roda numa thread (ver export_patient)."""
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
    # Primeira versão: sem o nome do profissional no documento (só a conta que
    # exportou, como era antes). Volta com SHOW_PROFESSIONAL_IDENTIFICATION=true.
    if SHOW_PROFESSIONAL_IDENTIFICATION:
        elems.append(Paragraph(f"Profissional: {pdf_text(professional_name)} · {pdf_text(professional_email)}", meta))
    else:
        elems.append(Paragraph(f"Exportado por: {pdf_text(professional_email)}", meta))
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
    # Termo por profissional: o documento exportado leva a EVIDÊNCIA do
    # consentimento (quando foi dado / revogado), não só o "Concordo".
    if consent_given_at:
        elems.append(Paragraph(f"<b>Consentimento registrado em:</b> {consent_given_at:%d/%m/%Y %H:%M} UTC", meta))
    elif consent_terms:
        elems.append(Paragraph("Consentimento registrado antes do controle de data (sem data disponível).", meta))
    if consent_revoked_at:
        elems.append(Paragraph(f"<b>Consentimento revogado em:</b> {consent_revoked_at:%d/%m/%Y %H:%M} UTC", meta))
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

    return buf


@api_router.get("/patients/{pid}/export")
async def export_patient(pid: str, format: str = Query("json"), user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    p = await _get_owned_patient(db, pid, user)

    stmt_r = select(Record).where(Record.patient_id == pid, Record.owner_id == user.user_id).order_by(desc(Record.session_datetime))
    records = (await db.execute(stmt_r)).scalars().all()

    pat = patient_public(p, user)
    # Escala: a exportação é COMPLETA por definição (portabilidade, LGPD art. 18),
    # então não é paginada; descriptografar centenas de evoluções vai para a thread.
    recs = await run_in_threadpool(lambda: [record_public(r) for r in records])

    await log_audit(db, user.user_id, user.email, "exportar", "paciente", pid, f"Formato {format}")

    if format == "json":
        payload = {"paciente": pat, "prontuarios": recs,
                   "exportado_em": now_utc().isoformat(), "profissional": user.email}
        data = await run_in_threadpool(lambda: json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        return StreamingResponse(io.BytesIO(data), media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="prontuario_{pid}.json"'})

    # ESCALA (event loop): o ReportLab é puro Python e leva de dezenas de ms a
    # alguns segundos num prontuário longo. Antes ele rodava direto na rota
    # `async`, congelando o worker inteiro durante a geração. Agora roda numa
    # thread. Recebe só dados prontos (dicts e datas), nunca objetos do ORM,
    # para não haver acesso ao banco fora do event loop.
    buf = await run_in_threadpool(
        _render_patient_pdf, pat, recs, user.name, user.email,
        p.consent_given_at, bool(p.consent_terms), p.consent_revoked_at,
    )

    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="prontuario_{pid}.pdf"'})

@api_router.get("/audit")
async def get_audit(
    cursor: Optional[str] = Query(None, description="next_cursor da página anterior"),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    # ESCALA: antes mostrava só os 100 últimos registros, sem como ver os
    # anteriores (a trilha só cresce: inclui leituras). Agora é paginada pelo
    # índice ix_audit_logs_owner_timestamp.
    stmt = select(AuditLog).where(AuditLog.owner_id == user.user_id)
    if cursor:
        moment, row_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(AuditLog.timestamp, AuditLog.log_id) < (moment, row_id))
    stmt = stmt.order_by(desc(AuditLog.timestamp), desc(AuditLog.log_id)).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    docs, next_cursor = _split_page(rows, limit, lambda a: a.timestamp)

    return {"next_cursor": next_cursor, "items": [{
        "log_id": a.log_id,
        "action": a.action,
        "entity_type": a.entity_type,
        "entity_id": a.entity_id,
        "detail": a.detail,
        # Item 14: o profissional vê de onde veio cada ação (ex.: um acesso de
        # IP desconhecido é o primeiro sinal de conta comprometida).
        "ip_address": a.ip_address or "",
        "timestamp": a.timestamp.isoformat() if a.timestamp else ""
    } for a in docs]}

# Webhook do Google Forms
# (Header já importado no topo do arquivo)

class WebhookPayload(BaseModel):
    patient_data: PatientInput

# Item 9: o token do webhook é o único segredo desta rota pública. O limite
# impede tanto adivinhar tokens quanto inundar a base com pacientes falsos
# caso o token vaze. 30/min comporta com folga um formulário real.
@api_router.post("/webhook/google-forms", dependencies=[Depends(rate_limit("webhook", 30, 60))])
async def google_forms_webhook(payload: WebhookPayload, x_webhook_token: str = Header(...), db: AsyncSession = Depends(get_db)):
    # Item 15: o banco só tem o HASH do token; comparamos hash com hash.
    # A busca é por igualdade em coluna indexada, sem comparar caractere a
    # caractere em Python, então não há vazamento útil por tempo de resposta.
    stmt = select(User).where(User.webhook_token_hash == hash_token(x_webhook_token))
    owner = (await db.execute(stmt)).scalar_one_or_none()

    if not owner:
        raise HTTPException(status_code=401, detail="Token de integração inválido ou revogado")

    # Não exigimos aqui o aceite da versão vigente da política (get_active_user):
    # se a política mudar, bloquear o webhook faria o formulário do profissional
    # perder cadastros de pacientes em silêncio. O token só pode ter sido gerado
    # por alguém que já aceitou a política quando o gerou.
    data = payload.patient_data

    novo_paciente = Patient(owner_id=owner.user_id, anonymized=False)
    _apply_patient_input(novo_paciente, data)  # item 13: mesmos campos criptografados do cadastro manual
    db.add(novo_paciente)
    await _index_patient_name(db, novo_paciente, data.full_name)  # escala: busca no servidor
    # Termo por profissional: o consentimento marcado no formulário é
    # registrado com a cópia do termo ATUAL do profissional. Por isso a tela de
    # integração orienta a usar no formulário o mesmo texto de Configurações.
    consent_action = apply_consent(novo_paciente, data.consent_terms, owner)

    db.add(novo_paciente)
    await db.commit()
    await db.refresh(novo_paciente)

    # Item 13: sem o nome do paciente no detalhe (antes: "Integração Google Forms: <nome>").
    await log_audit(db, owner.user_id, owner.email, "criar", "paciente", novo_paciente.id, "Cadastro via Google Forms")
    if consent_action:
        await log_audit(db, owner.user_id, owner.email, consent_action, "paciente", novo_paciente.id,
                        "Termo de consentimento registrado (Google Forms)")

    return {"status": "sucesso", "paciente_id": novo_paciente.id}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@api_router.get("/dashboard/stats")
async def dashboard_stats(user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    uid = user.user_id
    # Item 12: pacientes arquivados não entram nas contagens nem na lista recente.
    active_patient = (Patient.owner_id == uid, Patient.archived_at.is_(None))

    # Contagens usando func.count()
    stmt_pacientes = select(func.count()).select_from(Patient).where(*active_patient, Patient.anonymized == False)
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
    stmt_recent_pat = select(Patient).where(*active_patient).order_by(desc(Patient.created_at)).limit(5)
    recent_patients_docs = (await db.execute(stmt_recent_pat)).scalars().all()

    # Uso do .isoformat() para garantir que o objeto date/datetime seja convertido para string no JSON
    recent_patients = [{
        "id": p.id,
        "full_name": decrypt_field(p.full_name),  # item 13
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
            "patient_name": decrypt_field(r.patient.full_name) if r.patient else "—",  # item 13
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
        # Item 13: título, nome do paciente e notas da agenda são criptografados.
        "patient_name": decrypt_field(s.patient_name),
        "title": decrypt_field(s.title),
        # O Frontend espera "start" e "end", mas no banco chamamos de start_time e end_time
        "start": s.start_time.isoformat() if s.start_time else "",
        "end": s.end_time.isoformat() if s.end_time else "",
        "status": s.status or "agendada",
        "notes": decrypt_field(s.notes),
    }


async def _apply_session_input(db: AsyncSession, user: User, s: Session, data: SessionInput) -> None:
    """Item 13: grava os campos livres da agenda criptografados (criar e editar).

    Produto (item 17 da auditoria): o paciente vinculado precisa ser DESTE
    profissional, e o nome gravado vem do cadastro (não do que o navegador
    mandou), para a agenda nunca mostrar um nome que não bate com o paciente.
    """
    patient_name = data.patient_name or ""
    if data.patient_id:
        patient = (await db.execute(select(Patient).where(
            Patient.id == data.patient_id, Patient.owner_id == user.user_id,
        ))).scalar_one_or_none()
        if not patient:
            raise HTTPException(status_code=422, detail="Paciente não encontrado para este profissional.")
        patient_name = decrypt_field(patient.full_name)

    s.patient_id = data.patient_id or None
    s.patient_name = encrypt_field(patient_name)
    s.title = encrypt_field(data.title)
    s.start_time = data.start  # Pydantic já entregou um datetime (UTC, com fuso)
    s.end_time = data.end      # Pydantic já entregou um datetime ou None
    s.status = data.status
    s.notes = encrypt_field(data.notes)


def _session_end(start: datetime, end: Optional[datetime]) -> datetime:
    """Sem término informado, a sessão dura o padrão (DEFAULT_SESSION_MINUTES)."""
    return _as_utc(end) if end else _as_utc(start) + timedelta(minutes=DEFAULT_SESSION_MINUTES)


async def _check_conflicts(db: AsyncSession, user: User, data: SessionInput, exclude_id: Optional[str] = None) -> None:
    """PRODUTO: detecção de conflito de horário (antes era possível marcar
    dois pacientes no mesmo horário sem nenhum aviso).

    Sessões canceladas não contam. A busca usa o índice (owner_id,
    start_time) e uma janela limitada pela duração máxima de uma sessão; a
    sobreposição exata é calculada aqui, igual no SQLite e no Postgres.
    """
    if data.status == "cancelada" or data.allow_conflict:
        return
    start, end = data.start, _session_end(data.start, data.end)
    stmt = select(Session).where(
        Session.owner_id == user.user_id,
        Session.status != "cancelada",
        Session.start_time < end,
        Session.start_time >= start - MAX_SESSION_DURATION,
    )
    if exclude_id:
        stmt = stmt.where(Session.id != exclude_id)
    candidates = (await db.execute(stmt)).scalars().all()
    conflicts = [c for c in candidates if _session_end(c.start_time, c.end_time) > start]
    if conflicts:
        raise HTTPException(status_code=409, detail={
            "code": "schedule_conflict",
            "msg": "Já existe sessão neste horário.",
            "conflicts": [session_public(c) for c in conflicts],
        })

# ---------------------------------------------------------------------------
# Rotas da Agenda (Sessions)
# ---------------------------------------------------------------------------
# ESCALA: a agenda é paginada por PERÍODO, que é como ela é consultada (o
# mês visível, as próximas sessões). Antes GET /sessions devolvia TODAS as
# sessões desde o primeiro dia de uso: depois de alguns anos, milhares de
# itens descriptografados a cada abertura da agenda.
MAX_SESSION_RANGE_DAYS = 400
MAX_SESSIONS_PER_REQUEST = 1000


@api_router.get("/sessions")
async def list_sessions(
    start: Optional[datetime] = Query(None, description="início do período (padrão: 30 dias atrás)"),
    end: Optional[datetime] = Query(None, description="fim do período, exclusivo (opcional)"),
    limit: int = Query(500, ge=1, le=MAX_SESSIONS_PER_REQUEST),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    start = _as_utc(start) if start else now_utc() - timedelta(days=30)
    end = _as_utc(end) if end else None
    if end is not None:
        if end <= start:
            raise HTTPException(status_code=400, detail="O fim do período deve ser depois do início.")
        if end - start > timedelta(days=MAX_SESSION_RANGE_DAYS):
            raise HTTPException(status_code=400, detail=f"Período máximo de {MAX_SESSION_RANGE_DAYS} dias.")

    # Usa o índice ix_sessions_owner_start. Ordena pela data de início
    # (crescente) para formar a agenda corretamente.
    stmt = select(Session).where(Session.owner_id == user.user_id, Session.start_time >= start)
    if end is not None:
        stmt = stmt.where(Session.start_time < end)
    stmt = stmt.order_by(Session.start_time.asc()).limit(limit)
    docs = (await db.execute(stmt)).scalars().all()

    return [session_public(s) for s in docs]

@api_router.post("/sessions")
async def create_session(data: SessionInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    await _check_conflicts(db, user, data)
    nova_sessao = Session(owner_id=user.user_id)
    await _apply_session_input(db, user, nova_sessao, data)
    db.add(nova_sessao)
    await db.commit()
    await db.refresh(nova_sessao)

    # Item 13: antes o detalhe era o título digitado (costuma conter o nome do paciente).
    await log_audit(db, user.user_id, user.email, "criar", "agenda", nova_sessao.id, "Sessão agendada")
    return session_public(nova_sessao)

@api_router.put("/sessions/{sid}")
async def update_session(sid: str, data: SessionInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Session).where(Session.id == sid, Session.owner_id == user.user_id)
    result = await db.execute(stmt)
    s = result.scalar_one_or_none()

    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    # A própria sessão não conta como conflito (remarcar 10 min para frente, por exemplo).
    await _check_conflicts(db, user, data, exclude_id=sid)
    await _apply_session_input(db, user, s, data)

    await db.commit()
    await db.refresh(s)

    # Item 14: alterações na agenda não eram auditadas.
    await log_audit(db, user.user_id, user.email, "editar", "agenda", sid, f"Sessão alterada (status: {data.status})")
    return session_public(s)

@api_router.delete("/sessions/{sid}")
async def delete_session(sid: str, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Session).where(Session.id == sid, Session.owner_id == user.user_id)
    result = await db.execute(stmt)
    s = result.scalar_one_or_none()

    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    await db.delete(s)
    await db.commit()

    # Item 14: exclusões na agenda também não eram auditadas.
    await log_audit(db, user.user_id, user.email, "excluir", "agenda", sid, "Sessão removida")
    return {"ok": True}

# ---------------------------------------------------------------------------
# Saúde e métricas (MONITORAMENTO)
# ---------------------------------------------------------------------------
# Sem login, sem limite de tentativas e sem auditoria: são consultadas a cada
# poucos segundos pelo Docker, pelo load balancer e pelo Prometheus.

@api_router.get("/health/live")
async def health_live():
    """O processo está de pé? (não toca no banco). Se falhar, reinicie o container."""
    return {"status": "ok"}


@api_router.get("/health/ready")
async def health_ready():
    """Pode receber tráfego? Confere o banco com prazo curto. Se falhar, o
    load balancer tira esta instância da rotação até ela se recuperar, em
    vez de mandar usuários para um worker que só responderia erro."""
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=2)
    except Exception:
        logger.exception("Health check: banco de dados indisponível.")
        return Response(content='{"status":"unavailable","database":"down"}', status_code=503,
                        media_type="application/json")
    return {"status": "ok", "database": "up"}


METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "").strip()


@api_router.get("/metrics", include_in_schema=False)
async def metrics(authorization: str = Header(default="")):
    """Métricas no formato Prometheus.

    Não são públicas: revelam volume de uso e rotas. Em produção exigem
    `Authorization: Bearer <METRICS_TOKEN>`; sem o token configurado, a rota
    nem existe (404). Em desenvolvimento fica aberta.
    """
    if ENVIRONMENT == "production" or METRICS_TOKEN:
        expected = f"Bearer {METRICS_TOKEN}"
        if not METRICS_TOKEN or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=404, detail="Not Found")
    content, content_type = await run_in_threadpool(observability.render_metrics)
    return Response(content=content, media_type=content_type)


app.include_router(api_router)