import asyncio
import base64
import csv
import hashlib
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
import uuid  # fluxo clínico (P1): identificador da série de sessões
from contextlib import asynccontextmanager
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
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import delete, desc, exists, func, or_, select, text, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from starlette.concurrency import run_in_threadpool
from xml.sax.saxutils import escape

import legal
import billing  # produto: assinatura (regras em billing.py)
import admin_access  # produto: administração (quem é administrador: ADMIN_EMAILS)
import mailer
import observability
import totp
from crypto import (
    SEARCH_INDEX_VERSION, contact_search_tokens, hash_token, is_fernet_token, load_fernet, load_search_key,
    name_search_tokens, query_contact_tokens, query_search_tokens,
)
import rate_limit as rate_limiting
from rate_limit import client_ip, enforce, rate_limit, require_shared_rate_limit
from database import AsyncSessionLocal, get_db, engine, Base
from environment import get_environment
from http_security import (
    BodySizeLimitMiddleware, CSRFMiddleware, SecurityHeadersMiddleware, integrity_error_handler,
    validation_error_handler,
)
from models import (
    AuditLog, BillingEventLog, EmailToken, Patient, PatientIntake, PatientSearchToken, Record, RecordVersion,
    Session, User, UserSession, now_utc,
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

# SEGURANÇA (varredura, item 14): segredo curto demais é adivinhável por força
# bruta OFFLINE: quem tiver UM token (ex.: copiado de um computador) testa
# bilhões de segredos por segundo até achar o que reproduz a assinatura, e
# depois assina tokens à vontade. A RFC 7518 pede ao menos 32 bytes para HS256.
JWT_SECRET_MIN_LENGTH = 32
if ENVIRONMENT == "production" and len(JWT_SECRET) < JWT_SECRET_MIN_LENGTH:
    logging.critical("SECURITY ALERT: JWT_SECRET precisa ter pelo menos %d caracteres aleatórios. Abortando.",
                     JWT_SECRET_MIN_LENGTH)
    sys.exit(1)

# SEGURANÇA (varredura, item 14): claims OBRIGATÓRIAS no token de login.
# Antes o jwt.decode aceitava qualquer token assinado com o segredo, mesmo sem
# validade (`exp`) ou sem a sessão (`sid`). Agora o token precisa dizer:
#   - quem emitiu (`iss`) e para quem (`aud`): um token emitido por outro
#     sistema que, por descuido, use o mesmo segredo não é aceito aqui;
#   - quando foi emitido (`iat`) e quando vence (`exp`);
#   - de quem é (`sub`) e qual sessão representa (`sid`).
# Falta qualquer uma = token recusado (o usuário entra de novo).
JWT_ISSUER = "psigestao-api"
JWT_AUDIENCE = "psigestao-web"
JWT_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "sid"]

# CORREÇÃO DE SEGURANÇA (item 2 da auditoria):
# A chave de criptografia agora vem SOMENTE de FERNET_KEY (+ FERNET_OLD_KEYS
# durante uma rotação), é validada na inicialização e é independente do
# JWT_SECRET. O objeto é criado uma única vez, e não a cada campo como antes.
# Detalhes e regras de fallback em crypto.py.
_fernet = load_fernet(ENVIRONMENT)

# ESCALA (paginação/busca): chave do índice cego de busca por nome, separada
# da FERNET_KEY. Obrigatória em produção. Detalhes em crypto.py.
_search_key = load_search_key(ENVIRONMENT)


def auth_cookie_name() -> str:
    """Nome do cookie de sessão.

    SEGURANÇA (varredura, item 18): em produção o nome leva o prefixo
    `__Host-`. O navegador SÓ aceita um cookie com esse prefixo se ele vier com
    Secure, Path=/ e SEM Domain. Na prática: o cookie fica preso a este
    endereço exato e nenhum outro subdomínio do mesmo domínio (um blog, um
    subdomínio esquecido apontando para outro serviço) consegue gravar um
    cookie com este nome por cima do nosso ("cookie tossing", usado para
    forçar a vítima a usar uma sessão do atacante).
    No desenvolvimento (http://localhost, sem HTTPS) o nome fica sem prefixo.
    Lido a cada chamada, e não fixado na importação, para os testes de produção
    (que trocam ENVIRONMENT) valerem também aqui.
    """
    return "__Host-access_token" if ENVIRONMENT == "production" else "access_token"


def clear_auth_cookie(response: Response) -> None:
    """Remove o cookie de sessão do navegador (logout e sessão encerrada).

    Os atributos precisam bater com os do set_cookie, senão alguns navegadores
    não removem o cookie `secure` em produção. Antes este bloco era repetido em
    cada rota; agora o nome com prefixo (acima) fica num lugar só.
    """
    is_prod = ENVIRONMENT == "production"
    response.delete_cookie(auth_cookie_name(), path="/", httponly=True,
                           samesite="strict" if is_prod else "lax", secure=is_prod)


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
        key=auth_cookie_name(),  # varredura, item 18: "__Host-access_token" em produção
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """O que roda ao iniciar o processo (antes do `yield`) e ao encerrar (depois).

    DEPENDÊNCIAS (varredura, item 13): antes era `@app.on_event("startup")`.
    Na atualização do FastAPI (0.110 -> 0.142) e do Starlette (0.37 -> 1.7),
    o on_event passou a ser obsoleto e vai deixar de existir; `lifespan` é o
    substituto oficial. O comportamento é o mesmo.
    """
    if AUTO_CREATE_TABLES:
        # Isso instrui o SQLAlchemy a criar todas as tabelas que ainda não existem
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    # Varredura, itens 8 e 15: um REDIS_URL errado apareceria só como "sem limite".
    await rate_limiting.check_redis()
    # Análise da administração (AD4): ADMIN_EMAILS que não bate com nenhuma conta pronta.
    await _log_admin_status()
    # Segurança média: rotina de manutenção (expurgo de contas encerradas no fim
    # da guarda e limpeza dos links de cadastro antigos). Ver run_maintenance.
    maintenance = asyncio.create_task(_maintenance_loop()) if MAINTENANCE_ENABLED else None
    yield
    if maintenance:
        maintenance.cancel()


# 1. Inicializa o app UMA ÚNICA VEZ
app = FastAPI(title="PsiGestão API", lifespan=lifespan, **api_docs_urls(ENVIRONMENT))

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
    # SEGURANÇA (varredura, item 19): em produção só origens HTTPS. Uma origem
    # http:// liberada aceitaria, com o cookie de sessão, uma página que pode
    # ser adulterada no caminho (Wi-Fi público) e então agir em nome do usuário.
    if ENVIRONMENT == "production" and any(not o.startswith("https://") for o in parsed):
        logging.critical("SECURITY ALERT: em produção CORS_ORIGINS só aceita origens https://.")
        sys.exit(1)
    return parsed


origins = _load_cors_origins()


def frontend_link(path: str, token: Optional[str] = None) -> str:
    """Endereço de uma tela do frontend, para os links enviados por e-mail.

    Base: FRONTEND_URL ou, sem ela, a primeira origem de CORS_ORIGINS (que já
    é o endereço do frontend, e em produção só aceita https://). O token vai
    no FRAGMENTO (#token=...): o navegador não envia o fragmento a servidor
    nenhum, então o link não fica gravado em logs de acesso, proxies ou no
    cabeçalho Referer.
    """
    base = (os.environ.get("FRONTEND_URL", "").strip() or origins[0]).rstrip("/")
    return f"{base}{path}#token={token}" if token else f"{base}{path}"

# SEGURANÇA (varredura, item 3): limite de tamanho do corpo (detalhes em
# http_security.py). Registrado ANTES do CORS para ficar por dentro dele: a
# resposta 413 também leva os cabeçalhos de CORS, e o frontend consegue ler
# a mensagem em vez de ver um erro de rede genérico.
app.add_middleware(BodySizeLimitMiddleware)

# SEGURANÇA (varredura, item 10): proteção contra CSRF (origem permitida +
# cabeçalho X-Requested-With nas escritas; detalhes em http_security.py).
# Fica por dentro do CORS, para a recusa (403) chegar legível ao frontend.
app.add_middleware(CSRFMiddleware, allowed_origins=origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    # SEGURANÇA (varredura, item 19): antes "*" (qualquer método e qualquer
    # cabeçalho). Agora só o que o frontend usa: um site permitido por engano
    # na lista, ou um script injetado nele, tem menos o que explorar.
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Requested-With", "X-Request-ID"],
    # Monitoramento: o navegador só deixa o frontend ler o ID da requisição
    # (para mostrar ao usuário ao reportar um erro) se ele for exposto aqui.
    # Content-Disposition: o nome dos arquivos baixados (ZIP da conta, CSV da
    # auditoria), que leva a data; sem expor, o navegador esconde o cabeçalho.
    expose_headers=["X-Request-ID", "Content-Disposition"],
    # O navegador guarda a licença do preflight por 10 min, em vez de
    # perguntar de novo antes de cada escrita.
    max_age=600,
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
# SEGURANÇA (varredura, item 12): cabeçalhos de segurança em TODA resposta,
# inclusive as do CORS e as 413 (por isso fica por fora deles). O HSTS só em
# produção, que roda atrás de HTTPS. Detalhes em http_security.py.
app.add_middleware(SecurityHeadersMiddleware, hsts=ENVIRONMENT == "production")
# Adicionado por último = camada mais externa: mede o tempo de TODA a
# requisição (inclusive CORS) e garante o X-Request-ID em todas as respostas.
app.add_middleware(observability.RequestContextMiddleware)

# SEGURANÇA (varredura, itens 3 e 12): o erro 422 diz qual campo está errado e
# por quê, sem devolver o valor enviado (senha, CPF, prontuário). Ver http_security.py.
app.add_exception_handler(RequestValidationError, validation_error_handler)
# SEGURANÇA (varredura, item 9): gravações simultâneas que batem numa regra de
# unicidade do banco viram 409 com mensagem clara, em vez de erro 500.
app.add_exception_handler(IntegrityError, integrity_error_handler)

# Monitoramento: Sentry (só com SENTRY_DSN) e exigência de rate limit
# compartilhado entre workers (ver rate_limit.py).
observability.init_sentry(ENVIRONMENT)
# Varredura, itens 8 e 15: em produção o servidor não sobe sem rate limit compartilhado (Redis).
require_shared_rate_limit(ENVIRONMENT)
# Produto (assinatura): configuração inválida, ou pagamento SIMULADO em
# produção (liberaria assinaturas sem cobrar), impede a subida.
billing.validate_config(ENVIRONMENT)
# Produto (administração): ADMIN_EMAILS com algo que não é e-mail impede a subida.
admin_access.validate_config(ENVIRONMENT)

# Aviso de conformidade: dados do controlador/encarregado em branco na política.
legal.warn_if_incomplete(ENVIRONMENT)
# Segurança média: cadastro e recuperação de senha dependem de e-mail; em
# produção o servidor não sobe sem SMTP criptografado (ver mailer.py).
mailer.check_mail_config(ENVIRONMENT)

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


# ---------------------------------------------------------------------------
# PRODUTO (análise da administração, AD2): MODO RESTRITO da conta suspensa.
#
# Antes, suspensa = ninguém entrava. A psicóloga ficava sem os prontuários
# (guarda obrigatória, Resolução CFP nº 001/2009) e sem atender os pedidos dos
# pacientes (LGPD art. 18); a administração, por desenho, também não vê esses
# dados, e ninguém conseguia atender o paciente.
# Agora a conta suspensa ENTRA (com senha e duas etapas, como sempre), mas o
# servidor só libera esta lista FECHADA: exportar (a conta inteira, a ficha de
# um paciente, a trilha), ver a lista de pacientes para escolher qual exportar,
# cuidar da segurança da conta e encerrá-la. Qualquer outra rota responde 403
# `account_suspended`, inclusive as que forem criadas depois: uma rota nova
# nasce BLOQUEADA aqui (fail-closed), e liberar é uma decisão explícita.
# A mensagem de suspensão só aparece depois do login (senha certa): quem não
# sabe a senha não descobre que a conta existe nem que está suspensa.
# ---------------------------------------------------------------------------
SUSPENDED_ALLOWED_ROUTES = frozenset({
    # sessão e aceite dos documentos
    ("GET", "/api/auth/me"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/accept-terms"),
    ("POST", "/api/auth/lock"), ("POST", "/api/auth/unlock"), ("POST", "/api/auth/activity"),
    # segurança da própria conta: só o que AUMENTA a proteção (ativar duas etapas,
    # novos códigos, encerrar aparelhos). Revisão de segurança (RS1): trocar a senha
    # e DESATIVAR as duas etapas saíram da lista. Não servem para exportar nem para
    # encerrar, e eram justamente o que alguém com a senha de outra pessoa faria
    # para tomar a conta. A senha nova sai pelo link do e-mail (rota pública).
    ("POST", "/api/auth/mfa/setup"), ("POST", "/api/auth/mfa/enable"), ("POST", "/api/auth/mfa/recovery-codes"),
    ("GET", "/api/auth/sessions"), ("POST", "/api/auth/sessions/{session_id}/end"),
    ("POST", "/api/auth/sessions/end-others"),
    # guarda do prontuário e pedidos dos pacientes: achar o paciente e exportar
    ("GET", "/api/patients"), ("POST", "/api/patients/search"), ("GET", "/api/patients/{pid}/export"),
    ("GET", "/api/account/export"),
    # transparência (o que a administração fez) e encerramento da conta (LGPD art. 18)
    ("GET", "/api/audit"), ("GET", "/api/audit/export"), ("POST", "/api/account/close"),
})
SUSPENDED_DETAIL = {
    "code": "account_suspended",
    "msg": "Sua conta está suspensa: só é possível exportar os dados, cuidar da segurança da conta e "
           "encerrá-la. O motivo foi enviado para o seu e-mail; para contestar, fale com o suporte (Ajuda).",
}


def _suspended_route_allowed(request: Request) -> bool:
    """A rota pedida está na lista do modo restrito? (pelo molde da rota, ex.:
    /api/patients/{pid}/export, e pelo método; rota sem molde = bloqueada)."""
    route = request.scope.get("route")
    return (request.method, getattr(route, "path", "")) in SUSPENDED_ALLOWED_ROUTES


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

    issued_at = now_utc()
    expires_at = issued_at + timedelta(hours=JWT_EXPIRE_HOURS)
    session_id = secrets.token_urlsafe(32)
    # SEGURANÇA (varredura, itens 7 e 14): o ID da sessão vai SÓ no JWT (cookie);
    # o banco guarda o hash dele. Ver UserSession.token_hash em models.py.
    db.add(UserSession(token_hash=hash_token(session_id), user_id=user.user_id, expires_at=expires_at,
                       last_activity_at=issued_at,  # produto: ponto de partida da inatividade
                       # Segurança média (sessões com aparelho): de onde a sessão foi aberta,
                       # para o profissional reconhecer (e encerrar) um acesso estranho.
                       device=describe_user_agent(_audit_user_agent.get()), ip_address=_audit_ip.get()))
    await db.commit()
    # AD2: entrada em conta suspensa fica registrada à parte (a sessão nasce em modo restrito).
    if user.suspended_at is not None:
        await log_audit(db, user.user_id, user.email, "entrar", "sessao", user.user_id,
                        "Entrada em conta suspensa (modo restrito: exportação, segurança e encerramento)")

    payload = {
        "sub": user.user_id,
        "sid": session_id,
        "exp": expires_at,
        # Segurança (varredura, item 14): claims agora obrigatórias na leitura.
        "iat": issued_at,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def end_login_sessions(db: AsyncSession, user: User, keep: Optional[UserSession] = None) -> int:
    """Encerra as sessões do usuário (todas, ou todas menos `keep`) e devolve
    quantas foram encerradas. Os tokens dessas sessões morrem na hora, em
    qualquer aparelho: a próxima requisição deles recebe 401.

    Segurança (varredura, item 5): usada na troca de senha, em "Sair dos
    outros dispositivos" e quando o login com Google descarta uma senha
    criada por quem não comprovou ser dono do e-mail.
    """
    stmt = delete(UserSession).where(UserSession.user_id == user.user_id)
    if keep is not None:
        stmt = stmt.where(UserSession.token_hash != keep.token_hash)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount or 0


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
    """Cadastro com senha, PASSO 1 (segurança média + backlog: confirmação de e-mail).

    A senha NÃO vem aqui: ela é criada no passo 2, depois de a pessoa abrir o
    link que chega no e-mail (SignupConfirmInput). Antes, o cadastro criava a
    conta na hora com a senha digitada, e isso permitia:
      - criar uma conta com o e-mail de outra pessoa (que depois a usaria com
        uma senha conhecida pelo atacante);
      - descobrir quais e-mails têm conta ("E-mail já cadastrado").
    """
    name: str
    email: EmailStr
    terms_accepted: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("Informe um nome entre 1 e 120 caracteres.")
        return value


class EmailLinkCheckInput(BaseModel):
    """Confere um link recebido por e-mail antes de mostrar o formulário."""
    token: str = Field(min_length=20, max_length=128)
    purpose: Literal["signup", "password_reset"]


class SignupConfirmInput(BaseModel):
    """Cadastro com senha, PASSO 2: o link do e-mail + a senha escolhida."""
    token: str = Field(min_length=20, max_length=128)
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_strength(value)


class ForgotPasswordInput(BaseModel):
    email: EmailStr


class ResetPasswordInput(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_strength(value)


class MfaLoginInput(BaseModel):
    """Login, PASSO 2 de quem tem verificação em duas etapas."""
    mfa_token: str = Field(max_length=2048)
    # 6 dígitos do aplicativo ou um código de recuperação ("k7m2-p9xq-4tbn-h3wd").
    code: str = Field(max_length=32)


class MfaCodeInput(BaseModel):
    code: str = Field(max_length=32)


class MfaDisableInput(BaseModel):
    code: str = Field(max_length=32)
    password: str = Field(max_length=256)


class CloseAccountInput(BaseModel):
    """Encerramento da conta: a pessoa digita ENCERRAR e confirma quem é."""
    confirmation: str = Field(max_length=32)
    password: Optional[str] = Field(default=None, max_length=256)
    credential: Optional[str] = Field(default=None, max_length=8192)
    code: Optional[str] = Field(default=None, max_length=32)


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
    # O ID Token JWT retornado pelo Google Identity Services no frontend.
    # Segurança (varredura, item 3): um token real tem ~1 a 2 KB.
    credential: str = Field(max_length=8192)


# ---------------------------------------------------------------------------
# SEGURANÇA (varredura, itens 2 e 3): limites e formatos dos dados do paciente.
# Antes nenhum campo tinha tamanho máximo, o e-mail aceitava qualquer texto e
# o CPF não era conferido: dava para gravar megabytes num campo (que ainda é
# criptografado, crescendo ~33%) e travar a geração do PDF. O frontend usa os
# MESMOS limites (frontend/src/lib/validation.js); quem decide é o servidor.
# ---------------------------------------------------------------------------
PATIENT_NAME_MAX = 150
PATIENT_NOTES_MAX = 10_000
RECORD_CONTENT_MAX = 50_000  # ~10 páginas por evolução: sobra para o uso clínico
RECORD_DIAGNOSIS_MAX = 5_000
_CPF_CHARS = re.compile(r"[\d.\-\s]+")
_PHONE_CHARS = re.compile(r"[\d\s()+.\-]+")


def cpf_is_valid(digits: str) -> bool:
    """Confere os dois dígitos verificadores do CPF (regra da Receita Federal)."""
    if len(digits) != 11 or digits == digits[0] * 11:  # 000.000.000-00 etc. passam na conta, mas não existem
        return False
    for size in (9, 10):
        total = sum(int(d) * weight for d, weight in zip(digits[:size], range(size + 1, 1, -1)))
        if (total * 10) % 11 % 10 != int(digits[size]):
            return False
    return True


# PRODUTO: TODOS os dados do cadastro do paciente são obrigatórios.
# Antes só o nome era exigido, e fichas incompletas (sem contato de emergência,
# sem telefone) chegavam ao atendimento. Rótulos na ordem do formulário, para a
# mensagem de erro listar o que falta como a pessoa vê na tela. O nome tem
# validação própria (validate_full_name) e a idade é CALCULADA pela data de
# nascimento (age_from_birth_date), por isso não estão aqui.
PATIENT_REQUIRED_LABELS = {
    "cpf": "CPF",
    "rg": "RG",
    "birth_date": "Data de nascimento",
    "education": "Escolaridade",
    "marital_status": "Estado civil",
    "profession": "Profissão",
    "phone": "Telefone",
    "email": "E-mail",
    "address": "Endereço",
    "emergency_contact": "Contato de emergência",
    "initial_notes": "Notas iniciais",
}

# PRODUTO (fluxo clínico, C3): paciente MENOR de 18 anos.
# Antes o cadastro exigia de uma criança estado civil, profissão, RG, e-mail e
# telefone próprios, e não tinha onde registrar o responsável legal, que é
# quem consente o atendimento (ECA; Código de Ética do Psicólogo, art. 8º).
# Para menores (idade calculada pela data de nascimento):
#   - saem: estado civil e profissão (não se aplicam);
#   - ficam opcionais: RG, e-mail e telefone do próprio menor (o contato é o
#     do responsável);
#   - entram, obrigatórios: nome, CPF, parentesco e telefone do responsável.
# CPF do menor continua obrigatório: desde 2017 ele consta na certidão de
# nascimento (Provimento CNJ nº 63/2017).
ADULT_AGE = 18
GUARDIAN_REQUIRED_LABELS = {
    "guardian_name": "Nome do responsável",
    "guardian_cpf": "CPF do responsável",
    "guardian_relationship": "Parentesco do responsável",
    "guardian_phone": "Telefone do responsável",
}
_NOT_REQUIRED_FOR_MINORS = ("rg", "marital_status", "profession", "phone", "email")
PATIENT_MINOR_REQUIRED_LABELS = {
    **{field: label for field, label in PATIENT_REQUIRED_LABELS.items() if field not in _NOT_REQUIRED_FOR_MINORS},
    **GUARDIAN_REQUIRED_LABELS,
}


def is_minor(birth_date: Optional[date], on: Optional[date] = None) -> bool:
    """Menor de 18 anos na data `on` (hoje, se omitida). Sem data: não se sabe (False)."""
    age = age_from_birth_date(birth_date, on)
    return age is not None and age < ADULT_AGE


def age_from_birth_date(birth_date: Optional[date], today: Optional[date] = None) -> Optional[int]:
    """Idade em anos completos. PRODUTO: a idade não é mais digitada; sai da data
    de nascimento, então nunca fica desatualizada nem contradiz a data."""
    if birth_date is None:
        return None
    today = today or date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


class PatientInput(BaseModel):
    full_name: str = Field(max_length=PATIENT_NAME_MAX)
    cpf: Optional[str] = Field(default="", max_length=20)
    rg: Optional[str] = Field(default="", max_length=20)
    birth_date: Optional[date] = None # Tipagem alterada para date nativo
    age: Optional[str] = Field(default="", max_length=20)
    education: Optional[str] = Field(default="", max_length=100)
    marital_status: Optional[str] = Field(default="", max_length=100)
    profession: Optional[str] = Field(default="", max_length=100)
    phone: Optional[str] = Field(default="", max_length=30)
    email: Optional[str] = Field(default="", max_length=254)  # limite do próprio padrão de e-mail
    address: Optional[str] = Field(default="", max_length=300)
    emergency_contact: Optional[str] = Field(default="", max_length=200)
    initial_notes: Optional[str] = Field(default="", max_length=PATIENT_NOTES_MAX)
    consent_terms: bool = False
    # Fluxo clínico (C3): responsável legal (obrigatório para menores de 18 anos).
    guardian_name: Optional[str] = Field(default="", max_length=PATIENT_NAME_MAX)
    guardian_cpf: Optional[str] = Field(default="", max_length=20)
    guardian_relationship: Optional[str] = Field(default="", max_length=60)
    guardian_phone: Optional[str] = Field(default="", max_length=30)

    @field_validator("*")
    @classmethod
    def reject_null_bytes(cls, value):
        # O PostgreSQL recusa o caractere nulo em texto: antes, um "\x00" num
        # campo virava erro 500 em vez de uma mensagem de validação.
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("Texto com caractere inválido.")
        return value

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Informe o nome completo do paciente.")
        return value

    @field_validator("cpf", "guardian_cpf")  # C3: o CPF do responsável segue a mesma regra
    @classmethod
    def validate_cpf(cls, value: Optional[str]) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        digits = re.sub(r"\D", "", value)
        if not _CPF_CHARS.fullmatch(value) or not cpf_is_valid(digits):
            raise ValueError("CPF inválido. Confira os 11 números.")
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"  # sempre no mesmo formato

    @field_validator("phone", "guardian_phone")  # C3: telefone do responsável também
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        digits = re.sub(r"\D", "", value)
        # 10 ou 11 dígitos com DDD; até 15 com código do país (padrão E.164).
        if not _PHONE_CHARS.fullmatch(value) or not 10 <= len(digits) <= 15:
            raise ValueError("Telefone inválido. Informe o DDD e o número, ex.: (11) 91234-5678.")
        return value

    @field_validator("email")
    @classmethod
    def validate_email_address(cls, value: Optional[str]) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        try:
            return validate_email(value, check_deliverability=False).normalized
        except EmailNotValidError:
            raise ValueError("E-mail inválido.") from None

    @field_validator("birth_date", mode="before")
    @classmethod
    def normalize_birth_date(cls, value):
        if isinstance(value, str):
            value = value.strip()
            # Campo de data vazio no formulário = paciente sem data informada.
            # Antes o "" ia direto para a conversão de data e o cadastro inteiro
            # falhava com um erro em inglês (achado nos testes da varredura, Fase 4).
            if not value:
                return None
            parts = value.replace("/", "-").split("-")
            if len(parts) == 3 and len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 4:
                try:
                    return date(int(parts[2]), int(parts[1]), int(parts[0]))
                except ValueError:
                    return value
        return value

    @field_validator("birth_date")
    @classmethod
    def validate_birth_date(cls, value: Optional[date]) -> Optional[date]:
        # Segurança (varredura, item 3): datas impossíveis (futuras ou de antes
        # de 1900) eram aceitas e apareciam no prontuário e no PDF.
        if value is not None and not date(1900, 1, 1) <= value <= date.today():
            raise ValueError("Data de nascimento inválida.")
        return value

    @model_validator(mode="after")
    def require_all_fields(self):
        """PRODUTO: nenhum campo do cadastro pode ficar em branco.

        Checado no MODELO (e não campo a campo) porque assim vale também para o
        campo que nem foi enviado, e a resposta lista de uma vez tudo o que falta.
        Espaços em branco contam como vazio. O frontend faz a mesma checagem antes
        de enviar (frontend/src/pages/Patients.js); quem decide é o servidor.
        """
        # C3: menor de 18 anos tem outra lista (sem estado civil/profissão, com o responsável).
        labels = PATIENT_MINOR_REQUIRED_LABELS if is_minor(self.birth_date) else PATIENT_REQUIRED_LABELS
        missing = [label for field, label in labels.items()
                   if not str(getattr(self, field) or "").strip()]
        if missing:
            raise ValueError("Preencha todos os campos do cadastro. Faltando: " + ", ".join(missing) + ".")
        return self


class IntakeSubmitData(PatientInput):
    """Produto (cadastro pelo paciente): a ficha enviada PELO PACIENTE pelo link.

    As MESMAS regras do cadastro manual (formato de CPF, telefone e e-mail,
    campos obrigatórios, menor de idade com responsável), com duas diferenças:
      - "Notas iniciais" é anotação clínica da psicóloga: o paciente não
        preenche (o que vier é descartado) e o campo não é exigido;
      - o termo de consentimento precisa de resposta explícita (sem valor
        padrão): "concordo" ou "não concordo", nunca um silêncio.
    Antes, com o Google Forms, a ficha entrava sem validação nenhuma (um CPF
    errado entrava como veio); aqui o paciente vê o erro e corrige na hora.
    """
    consent_terms: bool

    @field_validator("initial_notes")
    @classmethod
    def drop_initial_notes(cls, value: Optional[str]) -> str:
        return ""

    @model_validator(mode="after")
    def require_all_fields(self):
        """Mesmo nome do validador de PatientInput: o substitui, sem "Notas iniciais"."""
        labels = PATIENT_MINOR_REQUIRED_LABELS if is_minor(self.birth_date) else PATIENT_REQUIRED_LABELS
        missing = [label for field, label in labels.items()
                   if field != "initial_notes" and not str(getattr(self, field) or "").strip()]
        if missing:
            raise ValueError("Preencha todos os campos do cadastro. Faltando: " + ", ".join(missing) + ".")
        return self


class IntakeDraftData(BaseModel):
    """Produto (cadastro pelo paciente): o que já foi digitado, salvo enquanto o
    paciente preenche. Só LIMITES de tamanho (o rascunho pode estar pela metade);
    as regras completas valem no envio (IntakeSubmitData). Campos desconhecidos
    são ignorados: o rascunho guarda só o que a ficha tem."""
    full_name: str = Field(default="", max_length=PATIENT_NAME_MAX)
    cpf: str = Field(default="", max_length=20)
    rg: str = Field(default="", max_length=20)
    birth_date: str = Field(default="", max_length=10)
    education: str = Field(default="", max_length=100)
    marital_status: str = Field(default="", max_length=100)
    profession: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=30)
    email: str = Field(default="", max_length=254)
    address: str = Field(default="", max_length=300)
    emergency_contact: str = Field(default="", max_length=200)
    guardian_name: str = Field(default="", max_length=PATIENT_NAME_MAX)
    guardian_cpf: str = Field(default="", max_length=20)
    guardian_relationship: str = Field(default="", max_length=60)
    guardian_phone: str = Field(default="", max_length=30)
    consent_terms: Optional[bool] = None

    @field_validator("*")
    @classmethod
    def reject_null_bytes(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("Texto com caractere inválido.")
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
    version: str = Field(max_length=32)  # varredura, item 3: ex.: "2026-09-30"

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
    # Segurança (varredura, item 3): antes sem limite. Os limites cobrem com
    # folga o uso clínico e impedem gravar megabytes por evolução.
    content: str = Field(max_length=RECORD_CONTENT_MAX)
    diagnosis: Optional[str] = Field(default="", max_length=RECORD_DIAGNOSIS_MAX)
    # PRODUTO (agenda -> evolução): a sessão da agenda a que esta evolução se
    # refere, quando registrada a partir dela (painel "Hoje" ou agenda). Opcional.
    # Só vale ao CRIAR; na edição o vínculo não muda (ver update_record).
    session_id: Optional[str] = Field(default=None, max_length=64)

    @field_validator("session_datetime")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value)


class RecordUpdateInput(RecordInput):
    # Segurança (varredura, item 9): versão que o profissional ABRIU para
    # editar. Obrigatória: sem ela o servidor não teria como saber se outra
    # edição chegou antes (ver update_record).
    expected_version: int = Field(ge=1)


# PRODUTO (visões da agenda): regras que o servidor passa a garantir.
# Fluxo clínico (C4): "faltou" (o paciente não veio, sem avisar). Antes só havia
# "cancelada", e a falta se perdia: ela importa para o registro clínico, para
# acompanhar a adesão ao tratamento e para a cobrança.
SESSION_STATUSES = ("agendada", "realizada", "faltou", "cancelada")
# Sessões SEM atendimento: não contam como consulta, não ocupam o horário
# (não geram conflito) e não pedem evolução.
NO_SHOW_STATUSES = ("faltou", "cancelada")
SessionStatus = Literal["agendada", "realizada", "faltou", "cancelada"]
# Fluxo clínico (P1): sessões recorrentes. Até 52 de uma vez (um ano semanal).
RECURRENCE_DAYS = {"weekly": 7, "biweekly": 14}
RECURRENCE_LABELS = {"weekly": "semanal", "biweekly": "quinzenal"}
MAX_SERIES_SESSIONS = 52
# Duração padrão de uma sessão de psicoterapia, usada quando não há horário de
# término (inclusive na detecção de conflito). O frontend usa o mesmo valor.
DEFAULT_SESSION_MINUTES = 50
MAX_SESSION_DURATION = timedelta(hours=12)


class RepeatInput(BaseModel):
    """Fluxo clínico (P1): repetir a sessão nova. `count` inclui a primeira."""
    frequency: Literal["weekly", "biweekly"]
    count: int = Field(ge=2, le=MAX_SERIES_SESSIONS)


class SessionInput(BaseModel):
    # Segurança (varredura, item 3): limites também nos campos que faltavam.
    patient_id: Optional[str] = Field(default="", max_length=64)
    patient_name: Optional[str] = Field(default="", max_length=PATIENT_NAME_MAX)
    # PRODUTO (análise de UI, P6): o título deixou de ser obrigatório. Era quase
    # sempre "Sessão", digitado à mão a cada agendamento. Em branco, o servidor
    # gera: "Sessão" (com paciente) ou "Compromisso" (sem paciente: supervisão,
    # reunião). Quem quiser continua escrevendo um título ("Avaliação inicial").
    title: str = Field(default="", max_length=200)
    start: datetime # Tipagem alterada para datetime nativo
    end: Optional[datetime] = None # Tipagem alterada para datetime nativo
    # Antes aceitava qualquer texto (item 17 da auditoria); a agenda só conhece estes
    # (fluxo clínico, C4: + "faltou").
    status: SessionStatus = "agendada"
    notes: Optional[str] = Field(default="", max_length=5000)
    # Conflito de horário: o servidor recusa (409) uma sessão que se sobrepõe a
    # outra, a menos que o profissional confirme ("salvar mesmo assim").
    allow_conflict: bool = False
    # Fluxo clínico (P1): só na CRIAÇÃO, repetir a sessão (toda semana / a cada 2).
    repeat: Optional[RepeatInput] = None
    # Fluxo clínico (P1): só na EDIÇÃO de uma sessão de série: "this" (só esta)
    # ou "following" (esta e as próximas sessões AGENDADAS da série).
    apply_to: Literal["this", "following"] = "this"

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return (value or "").strip()  # P6: vazio vale; o título é gerado em validate_period

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
        # P1: só se repete sessão AGENDADA (as próximas ainda não aconteceram).
        if self.repeat is not None and self.status != "agendada":
            raise ValueError("Só uma sessão agendada pode se repetir.")
        # P6: título automático quando ficou em branco.
        if not self.title:
            self.title = "Sessão" if (self.patient_id or (self.patient_name or "").strip()) else "Compromisso"
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


class PasswordChangeInput(BaseModel):
    """Segurança (varredura, item 5): troca de senha.

    Quem tem senha confirma com a senha ATUAL. Quem só entra pelo Google (ou
    teve a senha descartada no vínculo com o Google) define a primeira senha
    confirmando com o Google. Assim, alguém que encontre um computador logado
    não consegue trocar a senha e tomar a conta.
    """
    current_password: Optional[str] = Field(default=None, max_length=256)
    credential: Optional[str] = Field(default=None, max_length=8192)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_strength(value)  # mesma política do cadastro


class EndSessionsInput(BaseModel):
    # Segurança (varredura, item 10): corpo JSON obrigatório, mesmo sem dado
    # útil. Rotas POST sem corpo dependem só do SameSite do cookie contra
    # CSRF; exigindo JSON, outro site não consegue montar a requisição sem
    # passar pela checagem do CORS.
    confirm: Literal[True]


# PRODUTO (análise de UI, P7): horário de atendimento padrão da grade da agenda
# (antes a grade tinha as 24 horas e abria rolada nas 07:00). Fim exclusivo.
DEFAULT_AGENDA_HOURS = (7, 21)


class AgendaSettingsInput(BaseModel):
    """P7: horário de atendimento (horas cheias). 0 a 24 mostra o dia inteiro."""
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=1, le=24)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_hour - self.start_hour < 2:
            raise ValueError("O horário de atendimento precisa ter pelo menos 2 horas.")
        return self


class OnboardingInput(BaseModel):
    """O1: ocultar (ou mostrar de novo) a lista "Primeiros passos" do painel."""
    dismissed: bool


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


def consent_by_guardian(p: Patient) -> bool:
    """C3: o consentimento foi dado pelo responsável legal? Sim quando o paciente
    era menor NA DATA do consentimento e há responsável cadastrado. Calculado
    (não gravado): a data de nascimento e a do consentimento já são a evidência."""
    if not p.consent_terms or not p.guardian_name:
        return False
    on = _as_utc(p.consent_given_at).date() if p.consent_given_at else None
    return is_minor(p.birth_date, on)


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
        # PRODUTO: idade calculada na leitura (nunca desatualizada); o valor
        # digitado só vale para fichas antigas ou do Google Forms sem data.
        "age": str(age_from_birth_date(p.birth_date)) if p.birth_date else (p.age or ""),
        "education": p.education or "",
        "marital_status": p.marital_status or "",
        "profession": p.profession or "",
        "phone": decrypt_field(p.phone),
        "email": decrypt_field(p.email),
        "address": decrypt_field(p.address),
        "emergency_contact": decrypt_field(p.emergency_contact),
        "initial_notes": decrypt_field(p.initial_notes),
        "consent_terms": p.consent_terms or False,
        "consent_terms_status": (
            ("Concordo (pelo responsável legal)" if consent_by_guardian(p) else "Concordo")
            if p.consent_terms else "Não concordo"
        ),
        # Fluxo clínico (C3): menor de idade e responsável legal.
        "is_minor": is_minor(p.birth_date),
        "guardian_name": decrypt_field(p.guardian_name),
        "guardian_cpf": decrypt_field(p.guardian_cpf),
        "guardian_relationship": p.guardian_relationship or "",
        "guardian_phone": decrypt_field(p.guardian_phone),
        "consent_by_guardian": consent_by_guardian(p),
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
    # PRODUTO: idade sai da data de nascimento (obrigatória no cadastro manual).
    p.age = str(age_from_birth_date(data.birth_date)) if data.birth_date else data.age
    p.education = data.education
    p.marital_status = data.marital_status
    p.profession = data.profession
    p.phone = encrypt_field(data.phone)
    p.email = encrypt_field(data.email)
    p.address = encrypt_field(data.address)
    p.emergency_contact = encrypt_field(data.emergency_contact)
    p.initial_notes = encrypt_field(data.initial_notes)
    # Fluxo clínico (C3): responsável legal (nome, CPF e telefone criptografados).
    p.guardian_name = encrypt_field((data.guardian_name or "").strip())
    p.guardian_cpf = encrypt_field(data.guardian_cpf)
    p.guardian_relationship = (data.guardian_relationship or "").strip()
    p.guardian_phone = encrypt_field(data.guardian_phone)

def record_public(r: Record) -> dict:
    return {
        "id": r.id,
        "patient_id": r.patient_id,
        # Conversão segura do DateTime para string ISO 8601
        "session_datetime": r.session_datetime.isoformat() if r.session_datetime else "",
        "content": decrypt_field(r.content) if r.content else "",
        "diagnosis": decrypt_field(r.diagnosis) if r.diagnosis else "",
        "version": r.version or 1,
        # Produto (agenda -> evolução): sessão da agenda ligada, se houver.
        "session_id": r.session_id or "",
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
        # Produto: tempo do bloqueio por inatividade e como desbloquear a tela
        # (senha para quem tem; Google para quem só entra pelo Google).
        "lock_minutes": u.lock_minutes or DEFAULT_LOCK_MINUTES,
        # Análise de UI (P7): horário de atendimento mostrado na grade da agenda.
        "agenda_start_hour": DEFAULT_AGENDA_HOURS[0] if u.agenda_start_hour is None else u.agenda_start_hour,
        "agenda_end_hour": DEFAULT_AGENDA_HOURS[1] if u.agenda_end_hour is None else u.agenda_end_hour,
        # Análise de UI (O1): passos da lista "Primeiros passos" que dependem da conta.
        # Termo personalizado antes desta versão também conta como revisado.
        "consent_term_reviewed": bool(u.consent_term_reviewed_at or u.consent_term_body),
        "onboarding_dismissed": u.onboarding_dismissed_at is not None,
        # Produto (assinatura): situação da conta (teste, assinante, atraso,
        # somente leitura, cortesia), preços e se o pagamento on-line existe.
        "subscription": billing.public_state(u),
        # AD2: conta suspensa (a tela do modo restrito mostra a data e o motivo,
        # o mesmo texto que a pessoa recebeu por e-mail). None = conta normal.
        "suspended": ({"at": _iso(_as_utc(u.suspended_at)), "reason": u.suspended_reason or ""}
                      if u.suspended_at else None),
        # Produto (administração): mostra "Administração" no menu. A permissão de
        # verdade é conferida em CADA rota /api/admin (get_admin).
        "is_admin": admin_access.is_admin(u),
        "has_password": bool(u.password_hash),
        # Segurança (backlog): verificação em duas etapas e e-mail comprovado.
        "mfa_enabled": u.mfa_enabled_at is not None,
        "mfa_recovery_codes_left": len(json.loads(u.mfa_recovery_codes or "[]")) if u.mfa_enabled_at else 0,
        "email_verified": u.email_verified_at is not None,
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
        # SEGURANÇA (varredura, item 14): além da assinatura e da validade,
        # exige todas as claims de JWT_REQUIRED_CLAIMS e confere emissor e
        # destinatário. Antes, um token sem `exp` (válido para sempre) ou sem
        # `sid` passava por esta etapa.
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER, audience=JWT_AUDIENCE,
            options={"require": JWT_REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError:
        return None

    session_id = payload["sid"]
    if not isinstance(session_id, str) or not session_id:
        return None

    # ESCALA: esta função roda em TODA requisição autenticada. Antes eram duas
    # idas ao banco (sessão, depois usuário); o joinedload traz os dois juntos.
    # Segurança (varredura, item 7): a busca é pelo HASH do ID (o banco não
    # guarda o ID verdadeiro).
    stmt = select(UserSession).options(joinedload(UserSession.user)).where(
        UserSession.token_hash == hash_token(session_id),
        UserSession.user_id == payload["sub"],
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
    token = request.cookies.get(auth_cookie_name())  # varredura, item 18: nome com __Host- em produção
    sess = await _session_from_token(token, db) if token else None
    if sess and sess.user:  # usuário já carregado junto com a sessão (joinedload)
        # AD2: conta suspensa só usa as rotas do modo restrito (todas as rotas
        # com login passam por aqui, inclusive /auth/me, sair e desbloquear).
        if sess.user.suspended_at is not None and not _suspended_route_allowed(request):
            raise HTTPException(status_code=403, detail=SUSPENDED_DETAIL)
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


async def get_current_session(request: Request, db: AsyncSession = Depends(get_db)) -> UserSession:
    """Sessão autenticada e DESBLOQUEADA.

    Sessão bloqueada responde 423 com code "session_locked": o frontend
    mostra a tela de bloqueio sem perder o que estava aberto (ex.: rascunho).
    Segurança (varredura, item 5): separada de get_current_user porque a
    troca de senha e "Sair dos outros dispositivos" precisam saber QUAL é a
    sessão atual, para encerrar as outras e manter esta.
    """
    sess = await _authenticated_session(request, db)
    if await _is_locked(db, sess):
        raise HTTPException(status_code=423, detail=SESSION_LOCKED_DETAIL)
    await _touch_activity(db, sess)
    return sess


async def get_current_user(sess: UserSession = Depends(get_current_session)) -> User:
    """Usuário autenticado com a sessão DESBLOQUEADA (todas as rotas de dados)."""
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


async def get_writer(user: User = Depends(get_active_user)) -> User:
    """Produto (assinatura): usuário ativo E com permissão de GRAVAR.

    Usada só nas rotas que criam ou editam o conteúdo de trabalho (paciente
    novo, evolução, sessão da agenda). No modo somente leitura (teste
    encerrado, atraso além da tolerância, assinatura encerrada) elas respondem
    402 com `code: read_only`; o frontend mostra o motivo e o caminho para
    assinar. Ver, EXPORTAR, corrigir o cadastro, anonimizar, arquivar,
    revogar consentimento e encerrar a conta continuam liberados: a psicóloga
    precisa cumprir a guarda do prontuário e os pedidos LGPD dos pacientes
    mesmo sem assinatura.
    """
    state = billing.access_state(user)
    if not state["can_write"]:
        raise HTTPException(status_code=402, detail={
            "code": "read_only", "reason": state["reason"], "msg": billing.READ_ONLY_MESSAGES[state["reason"]]})
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

# ---------------------------------------------------------------------------
# SEGURANÇA MÉDIA + backlog: links enviados por e-mail
# ---------------------------------------------------------------------------
# Usados no cadastro (confirmação do e-mail) e no "Esqueci minha senha". Cada
# link é de uso único, vence sozinho e pedir um novo invalida os anteriores.
# O banco guarda só o hash do token (models.EmailToken).
SIGNUP, PASSWORD_RESET = "signup", "password_reset"
SIGNUP_LINK_HOURS = 24
RESET_LINK_MINUTES = 60
INVALID_LINK_DETAIL = {"code": "invalid_link",
                       "msg": "Este link é inválido, já foi usado ou venceu. Peça um novo."}


async def _issue_email_token(db: AsyncSession, purpose: str, email: str, *, lifetime: timedelta,
                             user_id: Optional[str] = None, payload: Optional[dict] = None) -> str:
    """Cria um link novo (e apaga os anteriores do mesmo e-mail e finalidade)."""
    await db.execute(delete(EmailToken).where(EmailToken.email == email, EmailToken.purpose == purpose))
    token = secrets.token_urlsafe(32)
    db.add(EmailToken(
        purpose=purpose, token_hash=hash_token(token), email=email, user_id=user_id,
        payload=encrypt_field(json.dumps(payload, ensure_ascii=False)) if payload else "",
        expires_at=now_utc() + lifetime,
    ))
    await db.commit()
    return token


async def _valid_email_token(db: AsyncSession, token: str, purpose: str) -> EmailToken:
    row = (await db.execute(select(EmailToken).where(
        EmailToken.token_hash == hash_token(token), EmailToken.purpose == purpose,
    ))).scalar_one_or_none()
    if row is None or row.used_at is not None or _as_utc(row.expires_at) <= now_utc():
        raise HTTPException(status_code=400, detail=INVALID_LINK_DETAIL)
    return row


async def _consume_email_token(db: AsyncSession, row: EmailToken) -> None:
    """Marca o link como usado. Atômico: dois envios simultâneos do mesmo link
    (duplo clique) não passam os dois; o segundo recebe "link inválido"."""
    result = await db.execute(
        update(EmailToken).where(EmailToken.id == row.id, EmailToken.used_at.is_(None))
        .values(used_at=now_utc()).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=400, detail=INVALID_LINK_DETAIL)


def _token_payload(row: EmailToken) -> dict:
    try:
        return json.loads(decrypt_field(row.payload)) if row.payload else {}
    except ValueError:
        return {}


def _when_and_where() -> tuple[str, str]:
    """"Quando" e "onde" para os avisos de segurança por e-mail."""
    moment = now_utc()
    try:
        from zoneinfo import ZoneInfo
        when = moment.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y às %H:%M (horário de Brasília)")
    except Exception:  # sem base de fusos no sistema (ex.: Windows sem tzdata)
        when = moment.strftime("%d/%m/%Y às %H:%M (UTC)")
    ip = _audit_ip.get()
    where = describe_user_agent(_audit_user_agent.get()) + (f", IP {ip}" if ip else "")
    return when, where


def _security_notice(user: User, subject: str, what: str) -> "mailer.OutgoingEmail":
    when, where = _when_and_where()
    return mailer.security_notice(user.email, user.name or "", subject, what, when, where)


@api_router.post("/auth/register", dependencies=[Depends(rate_limit("register", 5, 600))])
async def register_user(data: RegisterInput, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Cadastro com senha, passo 1: manda o link de confirmação por e-mail.

    A resposta é SEMPRE a mesma ("enviamos um link"), exista ou não uma conta
    com o e-mail; quem já tem conta recebe, no e-mail, os atalhos para entrar
    ou recuperar a senha. Assim o cadastro não revela quais e-mails têm conta
    (antes: "E-mail já cadastrado."). O e-mail sai depois da resposta
    (BackgroundTasks), para o tempo de resposta também não revelar nada.
    """
    if not data.terms_accepted:
        raise HTTPException(status_code=400, detail="É necessário aceitar os Termos de Privacidade (LGPD).")

    email = data.email.lower().strip()
    # Além do limite por IP: no máximo 3 pedidos por e-mail por hora, para o
    # cadastro não virar ferramenta de encher a caixa de alguém de e-mails.
    await enforce(f"register-email:{email}", 3, 3600)

    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing:
        mail = mailer.signup_existing_account(email, frontend_link("/login"), frontend_link("/esqueci-senha"))
    else:
        token = await _issue_email_token(db, SIGNUP, email, lifetime=timedelta(hours=SIGNUP_LINK_HOURS), payload={
            "name": data.name,
            # Item 16: a versão da política aceita no formulário (evidência gravada na confirmação).
            "terms_version": legal.PRIVACY_POLICY_VERSION,
            "terms_accepted_at": now_utc().isoformat(),
        })
        mail = mailer.signup_confirmation(email, data.name, frontend_link("/criar-senha", token), SIGNUP_LINK_HOURS)
    background.add_task(mailer.send_email, mail)
    return {"status": "check_email", "email": email}


@api_router.post("/auth/email-link/check", dependencies=[Depends(rate_limit("email-link", 30, 600))])
async def check_email_link(data: EmailLinkCheckInput, db: AsyncSession = Depends(get_db)):
    """A tela de "criar senha"/"nova senha" confere o link antes de mostrar o
    formulário (e mostra para qual e-mail ele vale)."""
    row = await _valid_email_token(db, data.token, data.purpose)
    return {"email": row.email, "name": _token_payload(row).get("name", "")}


@api_router.post("/auth/register/confirm", dependencies=[Depends(rate_limit("register-confirm", 10, 600))])
async def confirm_signup(data: SignupConfirmInput, response: Response, db: AsyncSession = Depends(get_db)):
    """Cadastro com senha, passo 2: o link do e-mail + a senha. Só aqui a conta
    nasce, já com o e-mail comprovado (email_verified_at)."""
    row = await _valid_email_token(db, data.token, SIGNUP)
    payload = _token_payload(row)
    already = (await db.execute(select(User).where(User.email == row.email))).scalar_one_or_none()
    await _consume_email_token(db, row)
    if already:
        # A conta foi criada nesse meio-tempo (ex.: pelo login com Google).
        await db.commit()
        raise HTTPException(status_code=409, detail=(
            "Este e-mail já tem uma conta. Entre pelo login (ou use “Esqueci minha senha”)."))

    try:
        accepted_at = datetime.fromisoformat(payload.get("terms_accepted_at", ""))
    except ValueError:
        accepted_at = now_utc()
    new_user = User(
        email=row.email,
        name=payload.get("name", ""),
        password_hash=await hash_password_async(data.password),  # escala: fora do event loop
        auth_provider="email",
        terms_accepted=True,
        terms_version=payload.get("terms_version") or legal.PRIVACY_POLICY_VERSION,
        terms_accepted_at=accepted_at,
        # A pessoa abriu o link enviado a este e-mail: a posse está comprovada.
        email_verified_at=now_utc(),
        # Produto (assinatura): a conta nasce no teste grátis, sem cartão.
        trial_ends_at=now_utc() + timedelta(days=billing.TRIAL_DAYS),
    )
    db.add(new_user)
    try:
        await db.commit()
    except IntegrityError:
        # Varredura, item 9: duas confirmações simultâneas batem na regra de e-mail único.
        await db.rollback()
        raise HTTPException(status_code=409, detail=(
            "Este e-mail já tem uma conta. Entre pelo login (ou use “Esqueci minha senha”)."))
    await db.refresh(new_user)

    set_auth_cookie(response, await open_login_session(db, new_user))
    await log_audit(db, new_user.user_id, new_user.email, "criar", "usuario", new_user.user_id,
                    "Cadastro de profissional (e-mail confirmado)")
    # Evidência do aceite: versão aceita + data/IP (gravados pelo log_audit).
    await log_audit(db, new_user.user_id, new_user.email, "aceitar", "termos", new_user.user_id,
                    f"Política de Privacidade versão {new_user.terms_version}")
    return {"user": public_user(new_user)}


@api_router.post("/auth/password/forgot", dependencies=[Depends(rate_limit("forgot", 5, 600))])
async def forgot_password(data: ForgotPasswordInput, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """"Esqueci minha senha" (segurança média). Antes não existia: quem
    esquecia a senha perdia o acesso aos prontuários.

    Mesma resposta para qualquer e-mail (não revela quem tem conta). O link
    vale 1 hora, uma vez. Quem só entra pelo Google também pode usar: o link
    prova a posse do e-mail, e a pessoa passa a ter uma senha.
    """
    email = data.email.lower().strip()
    await enforce(f"forgot-email:{email}", 3, 3600)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user and user.closed_at is None:
        token = await _issue_email_token(db, PASSWORD_RESET, email, user_id=user.user_id,
                                         lifetime=timedelta(minutes=RESET_LINK_MINUTES))
        background.add_task(mailer.send_email, mailer.password_reset(
            email, user.name or "", frontend_link("/redefinir-senha", token), RESET_LINK_MINUTES,
            bool(user.password_hash)))
        # O dono vê na trilha que alguém pediu a troca (com IP), mesmo sem usar o link.
        await log_audit(db, user.user_id, user.email, "solicitar_senha", "usuario", user.user_id,
                        "Link de redefinição de senha enviado por e-mail")
    return {"status": "check_email"}


@api_router.post("/auth/password/reset", dependencies=[Depends(rate_limit("password-reset", 10, 600))])
async def reset_password(data: ResetPasswordInput, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Nova senha pelo link do e-mail. Encerra TODAS as sessões abertas (quem
    tinha a senha antiga perde o acesso) e NÃO entra direto: a pessoa faz o
    login normal, inclusive a verificação em duas etapas, se ativa."""
    row = await _valid_email_token(db, data.token, PASSWORD_RESET)
    user = await db.get(User, row.user_id) if row.user_id else None
    if user is None or user.closed_at is not None or user.email != row.email:
        raise HTTPException(status_code=400, detail=INVALID_LINK_DETAIL)
    await _consume_email_token(db, row)

    had_password = bool(user.password_hash)
    user.password_hash = await hash_password_async(data.new_password)
    # O link chegou no e-mail da conta: a posse do e-mail está comprovada.
    user.email_verified_at = user.email_verified_at or now_utc()
    await db.commit()

    ended = await end_login_sessions(db, user)
    # Outros links de redefinição que ainda estivessem valendo morrem aqui.
    await db.execute(delete(EmailToken).where(EmailToken.user_id == user.user_id,
                                              EmailToken.purpose == PASSWORD_RESET))
    await db.commit()
    await log_audit(db, user.user_id, user.email, "alterar_senha", "usuario", user.user_id,
                    f"Senha {'redefinida' if had_password else 'criada'} pelo link enviado por e-mail; "
                    f"{count_label(ended, 'sessão encerrada', 'sessões encerradas')}")
    background.add_task(mailer.send_email, _security_notice(
        user, "Senha alterada", "A senha da sua conta foi redefinida pelo link enviado por e-mail."))
    return {"ok": True}


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
        # SEGURANÇA (auditoria de login): antes os logins não entravam na trilha.
        # Uma senha errada numa conta EXISTENTE fica registrada (com IP e
        # navegador), para o profissional perceber tentativas de acesso. E-mail
        # inexistente não tem dono a quem mostrar; a resposta é a mesma de
        # sempre, para não revelar quais e-mails têm conta.
        if user:
            # RS1: senha desativada pela suspensão tem motivo próprio na trilha.
            reason = ("senha incorreta" if user.password_hash else
                      "senha desativada pela suspensão" if user.suspended_at else
                      "conta sem senha (entra pelo Google)")
            await log_audit(db, user.user_id, user.email, "entrar_falha", "sessao", user.user_id,
                            # Análise de UI (F3): termos em português na trilha ("Entrada", não "Login").
                            f"Entrada recusada: {reason} ({describe_user_agent(_audit_user_agent.get())})")
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")

    if user.mfa_enabled_at is not None:
        # Segurança (backlog: MFA): senha certa, mas a sessão só nasce depois
        # do código do aplicativo (POST /auth/login/mfa). Nada de cookie aqui.
        return {"mfa_required": True, "mfa_token": _mfa_login_token(user)}

    set_auth_cookie(response, await open_login_session(db, user))
    # Auditoria de login: cada acesso aparece em Configurações > Trilha de auditoria.
    await log_audit(db, user.user_id, user.email, "entrar", "sessao", user.user_id,
                    f"Entrada com senha ({describe_user_agent(_audit_user_agent.get())})")  # F3: em português

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
    # Segurança (varredura, item 5): o `sub` (identificador fixo da conta
    # Google) passou a ser a identidade usada no vínculo. Todo ID token
    # legítimo do Google o tem; sem ele, o token é recusado.
    if not id_info.get("sub"):
        raise HTTPException(status_code=401, detail="Token de autenticação inválido ou expirado.")
    return id_info


def describe_user_agent(user_agent: str) -> str:
    """"Chrome no Windows", a partir do cabeçalho User-Agent.

    SEGURANÇA (auditoria de login): só para leitura humana na trilha. O texto
    completo do User-Agent continua gravado na coluna user_agent. A ordem das
    checagens importa: o User-Agent do Edge também contém "Chrome", o do Chrome
    contém "Safari", o do Android contém "Linux" e o do iPhone contém "Mac OS X".
    """
    ua = user_agent or ""
    browsers = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
    systems = (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
               ("Mac OS X", "macOS"), ("Linux", "Linux"))
    browser = next((name for token, name in browsers if token in ua), None)
    system = next((name for token, name in systems if token in ua), None)
    # Sem nenhum dos dois (script, app, cURL): uma frase só, em vez de
    # "navegador não identificado no sistema não identificado".
    if not browser and not system:
        return "dispositivo não identificado"
    return f"{browser or 'navegador não identificado'} no {system or 'sistema não identificado'}"


def count_label(count: int, singular: str, plural: str) -> str:
    """"1 sessão encerrada" / "2 sessões encerradas".

    Correção (varredura de UI, defeito 9): mensagens e detalhes da auditoria
    usavam "sessão(ões)", "tentativa(s)", "registro(s)". Mesma regra do
    frontend (countLabel em frontend/src/lib/utils.js).
    """
    return f"{count} {singular if count == 1 else plural}"


def google_identity_matches(id_info: dict, user: User) -> bool:
    """A conta Google do token é a MESMA do usuário? (desbloqueio e troca de senha)

    Segurança (varredura, item 5): antes só o e-mail era comparado. Com o
    `sub` guardado, também exige a mesma conta Google: um e-mail reaproveitado
    por outra pessoa não passa.
    """
    same_email = id_info.get("email", "").lower().strip() == user.email
    return same_email and (not user.google_sub or id_info.get("sub") == user.google_sub)


@api_router.post("/auth/google", dependencies=[Depends(rate_limit("google", 10, 60))])
async def google_auth(data: GoogleAuthInput, response: Response, db: AsyncSession = Depends(get_db)):
    id_info = await verify_google_credential(data.credential)  # passos 1 e 2 (ver acima)
    email = id_info["email"].lower().strip()
    google_sub = id_info["sub"]
    name = id_info.get("name", "")
    picture = id_info.get("picture", "")

    # 3. Qual conta é esta?
    # SEGURANÇA (varredura, item 5): sequestro de conta pelo login com Google.
    # Antes a conta era achada SÓ pelo e-mail, e o login com Google entrava em
    # qualquer conta com aquele e-mail. Como o cadastro com senha não confirma
    # o e-mail, o ataque era:
    #   1. o atacante cadastra o e-mail (público) de um(a) psicólogo(a) com uma
    #      senha que só ele conhece;
    #   2. a vítima vê "E-mail já cadastrado" e entra pelo Google;
    #   3. cai na conta do atacante e cadastra os pacientes;
    #   4. o atacante entra com a senha e lê tudo.
    # Agora:
    #   - a conta é achada primeiro pelo `sub` do Google (identidade fixa);
    #   - um e-mail já vinculado a OUTRA conta Google é recusado;
    #   - se a conta tem senha e ninguém nunca comprovou ser dono do e-mail,
    #     o Google acaba de comprovar que o dono é QUEM ESTÁ ENTRANDO: a senha
    #     (que pode ter sido criada por outra pessoa) é descartada e TODAS as
    #     sessões abertas na conta são encerradas. O dono pode definir uma
    #     senha nova em Configurações, confirmando com o Google.
    user = (await db.execute(select(User).where(User.google_sub == google_sub))).scalar_one_or_none()
    password_discarded = False
    newly_linked = False
    created_account = False
    if user is None:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is not None:
            if user.google_sub and user.google_sub != google_sub:
                raise HTTPException(status_code=409, detail=(
                    "Este e-mail já está vinculado a outra conta Google. Entre com a conta Google original."
                ))
            if user.password_hash and user.email_verified_at is None:
                user.password_hash = None
                # O aceite da Política de Privacidade e o nome também vieram de
                # quem criou a conta, que pode não ser o dono: o aceite não serve
                # de evidência (LGPD) e o nome pode ser falso. O dono aceita a
                # política ele mesmo, e o nome passa a ser o da conta Google.
                user.terms_accepted = False
                user.terms_version = None
                user.terms_accepted_at = None
                user.name = name or user.name
                password_discarded = True
                await end_login_sessions(db, user)
            user.google_sub = google_sub
            user.email_verified_at = user.email_verified_at or now_utc()
            newly_linked = True

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
            # Segurança (varredura, item 5): identidade fixa + e-mail comprovado pelo Google.
            google_sub=google_sub,
            email_verified_at=now_utc(),
            # Produto (assinatura): a conta nasce no teste grátis, sem cartão.
            trial_ends_at=now_utc() + timedelta(days=billing.TRIAL_DAYS),
        )
        db.add(user)
        created_account = True

    await db.commit()
    await db.refresh(user)

    if newly_linked:
        detail = "Login com Google vinculado à conta (e-mail comprovado pelo Google)"
        if password_discarded:
            detail += "; senha sem e-mail comprovado descartada e sessões anteriores encerradas"
        await log_audit(db, user.user_id, user.email, "vincular", "usuario", user.user_id, detail)

    # 4. Geração da NOSSA sessão. Nunca dependemos da sessão do Google a longo prazo.
    set_auth_cookie(response, await open_login_session(db, user))
    # Auditoria de login (antes nem o cadastro pelo Google ficava registrado).
    if created_account:
        await log_audit(db, user.user_id, user.email, "criar", "usuario", user.user_id, "Cadastro com Google")
    await log_audit(db, user.user_id, user.email, "entrar", "sessao", user.user_id,
                    f"Entrada com Google ({describe_user_agent(_audit_user_agent.get())})")  # F3: em português

    # `password_discarded` permite ao frontend explicar por que a senha antiga
    # desta conta deixou de funcionar.
    return {"user": public_user(user), "password_discarded": password_discarded}

@api_router.get("/auth/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    # Item 15: antes esta rota criava um token de webhook para TODO usuário e o
    # devolvia a cada carregamento de página. A integração com o Google Forms
    # foi removida depois (substituída pelo link de cadastro do paciente).
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
                    # AD1: um aceite cobre os dois documentos (mesma versão).
                    f"Política de Privacidade e Termos de Uso versão {legal.PRIVACY_POLICY_VERSION}")
    return public_user(user)

@api_router.get("/legal/privacy-policy")
async def get_privacy_policy():
    # Pública (sem login): quem vai se cadastrar precisa poder ler a política
    # ANTES de aceitá-la. Texto e versão vêm de legal.py (fonte única).
    return legal.privacy_policy()


@api_router.get("/legal/terms-of-use")
async def get_terms_of_use():
    # Análise da administração (AD1): Termos de Uso, públicos como a política
    # (quem vai se cadastrar lê ANTES de aceitar). Mesma versão da política:
    # um aceite (POST /auth/accept-terms) cobre os dois documentos.
    return legal.terms_of_use()


@api_router.get("/legal/contact")
async def get_contact():
    # Análise de UI (O2): contatos da página de Ajuda (suporte e encarregado de
    # dados). Pública: quem não consegue entrar é quem mais precisa de ajuda.
    return legal.contact_info()

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
    token = request.cookies.get(auth_cookie_name())  # varredura, item 18: nome com __Host- em produção
    sess = await _session_from_token(token, db) if token else None
    if sess:
        user = sess.user
        await db.delete(sess)
        await db.commit()
        # Auditoria de login: a saída também fica registrada.
        await log_audit(db, user.user_id, user.email, "sair", "sessao", user.user_id, "Saída do sistema")  # F3

    clear_auth_cookie(response)
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
    this_session = UserSession.token_hash == sess.token_hash

    def session_ended() -> dict:
        clear_auth_cookie(response)
        # Lançar a exceção descartaria o delete_cookie acima; por isso a
        # resposta de erro é montada aqui, já com o cookie removido.
        response.status_code = 401
        return {"detail": {"code": "session_ended",
                           "msg": "Muitas tentativas. Por segurança, a sessão foi encerrada. Entre novamente."}}

    # SEGURANÇA (varredura, item 9): contador ATÔMICO, e a tentativa é
    # RESERVADA antes de conferir a senha.
    # Antes o contador era lido e somado em Python (ler -> +1 -> gravar) e só
    # depois de errar. Requisições em paralelo liam o mesmo valor e todas
    # conferiam a senha antes de qualquer erro ser gravado: 50 tentativas
    # simultâneas testavam 50 senhas, apesar do limite de 5.
    # Agora o próprio banco soma 1 (UPDATE ... SET n = n + 1 RETURNING n) e cada
    # requisição recebe um número único ANTES do bcrypt. Por mais paralelas
    # que sejam, no máximo MAX_UNLOCK_FAILURES senhas chegam a ser conferidas.
    attempt = (await db.execute(
        update(UserSession)
        .where(this_session)
        .values(unlock_failures=func.coalesce(UserSession.unlock_failures, 0) + 1)
        .returning(UserSession.unlock_failures)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    await db.commit()
    if attempt is None:  # a sessão foi encerrada por outra requisição neste meio-tempo
        return session_ended()
    if attempt > MAX_UNLOCK_FAILURES:  # excedente de uma rajada: nem confere a senha
        await db.execute(delete(UserSession).where(this_session))
        await db.commit()
        return session_ended()

    if data.password and user.password_hash:
        verified = await verify_password_async(data.password, user.password_hash)
    elif data.credential:
        id_info = await verify_google_credential(data.credential)
        # A conta do Google precisa ser A MESMA da sessão bloqueada (e-mail e,
        # desde a varredura, item 5, também o `sub` quando já vinculado).
        verified = google_identity_matches(id_info, user)
    else:
        verified = False

    if not verified:
        if attempt >= MAX_UNLOCK_FAILURES:
            await db.execute(delete(UserSession).where(this_session))
            await db.commit()
            await log_audit(db, user.user_id, user.email, "encerrar", "sessao", user.user_id,
                            "Sessão encerrada: limite de tentativas de desbloqueio")
            return session_ended()
        await log_audit(db, user.user_id, user.email, "desbloquear_falha", "sessao", user.user_id,
                        f"Tentativa de desbloqueio inválida ({attempt}/{MAX_UNLOCK_FAILURES})")
        remaining = MAX_UNLOCK_FAILURES - attempt
        raise HTTPException(status_code=403, detail={
            "code": "unlock_failed",
            "msg": (f"Senha incorreta. {'Resta' if remaining == 1 else 'Restam'} "
                    f"{count_label(remaining, 'tentativa', 'tentativas')} antes de a sessão ser encerrada."),
            "remaining": remaining,
        })

    # Desbloqueio também atômico: se a sessão foi encerrada enquanto a senha
    # era conferida (rajada acima), nenhuma linha é atualizada.
    unlocked = await db.execute(
        update(UserSession)
        .where(this_session)
        .values(locked_at=None, unlock_failures=0, last_activity_at=now_utc())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if unlocked.rowcount != 1:
        return session_ended()
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


@api_router.put("/auth/agenda-settings")
async def update_agenda_settings(data: AgendaSettingsInput, user: User = Depends(get_active_user),
                                 db: AsyncSession = Depends(get_db)):
    """Análise de UI (P7): horário de atendimento da grade da agenda."""
    user.agenda_start_hour = data.start_hour
    user.agenda_end_hour = data.end_hour
    await db.commit()
    await db.refresh(user)
    await log_audit(db, user.user_id, user.email, "editar", "usuario", user.user_id,
                    f"Horário de atendimento: {data.start_hour:02d}:00 às {data.end_hour:02d}:00")
    return public_user(user)


@api_router.put("/auth/onboarding")
async def update_onboarding(data: OnboardingInput, user: User = Depends(get_active_user),
                            db: AsyncSession = Depends(get_db)):
    """Análise de UI (O1): ocultar a lista "Primeiros passos" (ou trazê-la de
    volta, pela página de Ajuda). Preferência de tela: sem registro na auditoria."""
    user.onboarding_dismissed_at = now_utc() if data.dismissed else None
    await db.commit()
    await db.refresh(user)
    return public_user(user)


# ---------------------------------------------------------------------------
# PRODUTO: assinatura (planos, teste grátis, cortesia, somente leitura)
# ---------------------------------------------------------------------------
# As REGRAS ficam em billing.py; aqui ficam a gravação, a auditoria e as rotas.
#   POST /billing/checkout         -> endereço da página de pagamento do meio
#   POST /billing/cancel           -> cancela a renovação (vale até o fim do pago)
#   POST /billing/simulate*        -> só com o pagamento SIMULADO, em desenvolvimento
#   POST /webhook/billing/{meio}   -> avisos do meio de pagamento (pagou, atrasou...)
# A cortesia NÃO tem rota: é concedida no servidor (manage_billing.py), para não
# existir na API um caminho que libere acesso sem pagamento.
BILLING_SIM_AUDIENCE = "psigestao-billing-sim"
BILLING_SIM_MINUTES = 30
BILLING_ACTOR = "meio de pagamento"


class CheckoutInput(BaseModel):
    plan: Literal["monthly", "annual"]


class SimulatedPaymentInput(BaseModel):
    token: str = Field(max_length=2048)
    outcome: Literal["pagar", "recusar"]


class SimulatedEventInput(BaseModel):
    # renovar/atrasar/cancelar/estornar: avisos que um meio real mandaria.
    # vencer_periodo/vencer_tolerancia/encerrar_teste: "viagem no tempo" para
    # ver cada situação na tela sem esperar dias (só desenvolvimento).
    event: Literal["renovar", "atrasar", "cancelar", "estornar", "vencer_periodo", "vencer_tolerancia", "encerrar_teste"]


def _billing_checkout_token(user: User, plan: str) -> str:
    """Pagamento simulado: o "pedido" vai num token curto, com destinatário próprio
    (nunca aceito como sessão), preso à conta e ao plano escolhidos."""
    issued_at = now_utc()
    return jwt.encode({
        "sub": user.user_id, "plan": plan, "iat": issued_at,
        "exp": issued_at + timedelta(minutes=BILLING_SIM_MINUTES),
        "iss": JWT_ISSUER, "aud": BILLING_SIM_AUDIENCE, "jti": secrets.token_urlsafe(12),
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _billing_checkout_claims(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER,
                          audience=BILLING_SIM_AUDIENCE,
                          options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]})
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail={
            "code": "checkout_expired", "msg": "Este pagamento expirou. Volte e escolha o plano de novo."})


def billing_provider() -> Optional[billing.BillingProvider]:
    """O meio de pagamento configurado (None = pagamento on-line indisponível)."""
    if billing.BILLING_PROVIDER == "simulado":
        return billing.SimulatedProvider(_billing_checkout_token, frontend_link)
    return None


def _require_simulated() -> None:
    """As rotas de simulação só existem com o pagamento simulado, em desenvolvimento."""
    if billing.BILLING_PROVIDER != "simulado" or ENVIRONMENT != "development":
        raise HTTPException(status_code=404, detail="Não encontrado.")


async def apply_billing_event(db: AsyncSession, user: User, event: billing.BillingEvent, provider_name: str,
                              actor: str = BILLING_ACTOR) -> bool:
    """Aplica um aviso do meio de pagamento UMA vez (idempotência por event_id)
    e registra na trilha. Devolve False se o aviso já tinha sido aplicado."""
    key = f"{provider_name}:{event.event_id}"
    if await db.get(BillingEventLog, key) is not None:
        return False
    detail = billing.apply_event(user, event)
    db.add(BillingEventLog(event_id=key, provider=provider_name, kind=event.kind, user_id=user.user_id))
    try:
        await db.commit()
    except IntegrityError:  # o mesmo aviso chegou duas vezes ao mesmo tempo
        await db.rollback()
        return False
    await db.refresh(user)
    await log_audit(db, user.user_id, actor, "assinatura", "assinatura", user.user_id, detail)
    return True


@api_router.post("/billing/checkout", dependencies=[Depends(rate_limit("billing-checkout", 10, 300))])
async def billing_checkout(data: CheckoutInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Começa a assinatura: devolve o endereço da página de pagamento do meio.
    Usa get_active_user (não get_writer): quem está no somente leitura PRECISA conseguir assinar."""
    state = billing.access_state(user)
    if state["state"] == "courtesy":
        raise HTTPException(status_code=409, detail={"code": "courtesy", "msg": "Sua conta é cortesia: não há o que pagar."})
    if state["state"] == "active" and not state["cancel_at_period_end"]:
        raise HTTPException(status_code=409, detail={"code": "already_active", "msg": "Você já tem uma assinatura ativa."})
    provider = billing_provider()
    if provider is None:
        raise HTTPException(status_code=503, detail={
            "code": "billing_unavailable",
            "msg": "O pagamento on-line ainda não está disponível. Fale com o suporte (página de Ajuda) para assinar."})
    url = await provider.start_checkout(user, data.plan)
    user.billing_provider = provider.name
    await db.commit()
    await log_audit(db, user.user_id, user.email, "assinatura", "assinatura", user.user_id,
                    f"Pagamento iniciado: plano {billing.PLAN_LABELS[data.plan]}")
    return {"url": url}


@api_router.post("/billing/cancel", dependencies=[Depends(rate_limit("billing-cancel", 10, 300))])
async def billing_cancel(data: EndSessionsInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Cancela a RENOVAÇÃO. O que já foi pago continua valendo até o fim do período;
    depois a conta vai para o somente leitura (sem tolerância: foi uma escolha)."""
    if user.subscription_status not in ("active", "past_due") or user.courtesy_at is not None:
        raise HTTPException(status_code=409, detail={"code": "no_subscription", "msg": "Não há assinatura ativa para cancelar."})
    provider = billing_provider()
    if provider is not None and user.billing_provider == provider.name:
        await provider.cancel(user)
    event = billing.BillingEvent(event_id=f"cancelamento-{uuid.uuid4().hex}", kind="subscription_canceled",
                                 user_id=user.user_id)
    await apply_billing_event(db, user, event, user.billing_provider or "app", actor=user.email)
    return public_user(user)


@api_router.post("/billing/simulate")
async def billing_simulate_payment(data: SimulatedPaymentInput, user: User = Depends(get_active_user),
                                   db: AsyncSession = Depends(get_db)):
    """Pagamento SIMULADO (desenvolvimento): a tela /assinatura/simulacao manda
    "pagar" ou "recusar". Pagar vira o mesmo aviso que um meio real mandaria; o
    id do aviso vem do token, então o mesmo pagamento nunca vale duas vezes."""
    _require_simulated()
    claims = _billing_checkout_claims(data.token)
    if claims["sub"] != user.user_id:
        raise HTTPException(status_code=403, detail={"code": "checkout_other_user",
                                                     "msg": "Este pagamento pertence a outra conta."})
    if data.outcome == "recusar":
        await log_audit(db, user.user_id, BILLING_ACTOR, "assinatura", "assinatura", user.user_id,
                        "Pagamento recusado (simulação)")
        return {"applied": False, "user": public_user(user)}
    event = billing.BillingEvent(event_id=f"checkout-{claims['jti']}", kind="payment_confirmed",
                                 user_id=user.user_id, plan=claims["plan"])
    applied = await apply_billing_event(db, user, event, "simulado")
    return {"applied": applied, "user": public_user(user)}


@api_router.post("/billing/simulate/event")
async def billing_simulate_event(data: SimulatedEventInput, user: User = Depends(get_active_user),
                                 db: AsyncSession = Depends(get_db)):
    """Ferramentas de teste do pagamento simulado (desenvolvimento): avisos do meio
    de pagamento e "viagem no tempo" para ver cada situação da conta na tela."""
    _require_simulated()
    now = now_utc()
    if data.event in ("vencer_periodo", "vencer_tolerancia", "encerrar_teste"):
        if data.event == "encerrar_teste":
            user.trial_ends_at = now - timedelta(seconds=1)
        elif not user.paid_until:
            raise HTTPException(status_code=409, detail={"code": "no_period",
                                                         "msg": "Não há período pago. Simule um pagamento antes."})
        elif data.event == "vencer_periodo":
            user.paid_until = now - timedelta(seconds=1)
        else:
            user.past_due_since = now - timedelta(days=billing.GRACE_DAYS, seconds=1)
            user.paid_until = min(_as_utc(user.paid_until), user.past_due_since)
        await db.commit()
        await db.refresh(user)
        await log_audit(db, user.user_id, BILLING_ACTOR, "assinatura", "assinatura", user.user_id,
                        f"Simulação: {data.event.replace('_', ' ')}")
        return public_user(user)
    kind = {"renovar": "payment_confirmed", "atrasar": "payment_overdue", "cancelar": "subscription_canceled",
            "estornar": "payment_refunded"}[data.event]
    event = billing.BillingEvent(event_id=f"evento-{uuid.uuid4().hex}", kind=kind, user_id=user.user_id,
                                 plan=user.subscription_plan)
    await apply_billing_event(db, user, event, "simulado")
    return public_user(user)


@api_router.post("/webhook/billing/{provider_name}", dependencies=[Depends(rate_limit("billing-webhook", 120, 60))])
async def billing_webhook(request: Request, provider_name: str = PathParam(max_length=32),
                          db: AsyncSession = Depends(get_db)):
    """Avisos do meio de pagamento (pagou, atrasou, cancelou, estornou).

    Fora do CSRF (prefixo /api/webhook/): quem chama é o servidor do meio de
    pagamento. A autenticidade é conferida por ele mesmo (parse_webhook: token
    ou assinatura do meio); aviso não autêntico = 401 e nada muda. Cada aviso
    vale uma vez (apply_billing_event). Responde 200 também para aviso repetido
    ou de conta desconhecida, senão o meio reenviaria para sempre.
    """
    provider = billing_provider()
    if provider is None or provider.name != provider_name or provider.name == "simulado":
        raise HTTPException(status_code=404, detail="Não encontrado.")
    try:
        events = provider.parse_webhook(dict(request.headers), await request.body())
    except PermissionError:
        raise HTTPException(status_code=401, detail="Aviso de pagamento não autenticado.")
    for event in events:
        user = await db.get(User, event.user_id) if event.user_id else None
        if user is None:
            logger.warning("Aviso de pagamento de conta desconhecida ignorado (%s).", event.kind)
            continue
        await apply_billing_event(db, user, event, provider.name)
    return {"ok": True}


def _apply_courtesy(user: User, granted: bool, reason: str) -> str:
    """Liga/desliga a cortesia (comando do servidor e tela de administração)."""
    reason = (reason or "").strip()[:120]
    user.courtesy_at = now_utc() if granted else None
    user.courtesy_reason = reason if granted else ""
    return reason


async def set_courtesy(db: AsyncSession, email: str, granted: bool, reason: str = "") -> dict:
    """Cortesia (manage_billing.py): acesso completo sem cobrança, até ser revogada.
    Só pelo servidor, com registro na trilha da própria conta."""
    user = (await db.execute(select(User).where(func.lower(User.email) == email.strip().lower()))).scalar_one_or_none()
    if user is None:
        raise LookupError(f"Nenhuma conta com o e-mail {email}.")
    reason = _apply_courtesy(user, granted, reason)
    await db.commit()
    await db.refresh(user)
    detail = ("Cortesia concedida" + (f" ({reason})" if reason else "")) if granted else "Cortesia revogada"
    await log_audit(db, user.user_id, "sistema (linha de comando)", "cortesia", "assinatura", user.user_id, detail)
    return billing.access_state(user)


# ---------------------------------------------------------------------------
# SEGURANÇA (varredura, item 5): troca de senha e "Sair dos outros dispositivos"
# ---------------------------------------------------------------------------
# Antes não havia como trocar a senha nem encerrar sessões em outros
# aparelhos: se uma senha vazasse, ou se um computador ficasse logado em
# outro lugar, o profissional não tinha o que fazer.

@api_router.post("/auth/password", dependencies=[Depends(rate_limit("password", 5, 900))])
async def change_password(data: PasswordChangeInput, background: BackgroundTasks,
                          sess: UserSession = Depends(get_current_session), db: AsyncSession = Depends(get_db)):
    user = sess.user
    # Além do limite por IP (acima), limite por CONTA: um cookie roubado usado
    # de vários IPs não ganha mais tentativas de adivinhar a senha atual.
    await enforce(f"password-user:{user.user_id}", 5, 900)

    had_password = bool(user.password_hash)
    if had_password:
        if not data.current_password or not await verify_password_async(data.current_password, user.password_hash):
            await log_audit(db, user.user_id, user.email, "alterar_senha_falha", "usuario", user.user_id,
                            "Troca de senha recusada: senha atual incorreta")
            raise HTTPException(status_code=403, detail={"code": "invalid_current_password",
                                                         "msg": "A senha atual está incorreta."})
        if await verify_password_async(data.new_password, user.password_hash):
            raise HTTPException(status_code=400, detail="A nova senha precisa ser diferente da atual.")
    else:
        if not data.credential:
            raise HTTPException(status_code=400, detail="Confirme sua identidade com o Google para definir a senha.")
        if not google_identity_matches(await verify_google_credential(data.credential), user):
            raise HTTPException(status_code=403, detail="Esta conta Google não é a da sessão atual.")

    user.password_hash = await hash_password_async(data.new_password)  # escala: fora do event loop
    await db.commit()

    # Quem soubesse a senha antiga (ou tivesse uma sessão aberta em outro
    # aparelho) perde o acesso na hora. Esta sessão continua aberta.
    ended = await end_login_sessions(db, user, keep=sess)
    await log_audit(db, user.user_id, user.email, "alterar_senha", "usuario", user.user_id,
                    f"Senha {'alterada' if had_password else 'definida'}; "
                    f"{count_label(ended, 'outra sessão encerrada', 'outras sessões encerradas')}")
    # Segurança média: o dono fica sabendo da troca mesmo se não foi ele.
    background.add_task(mailer.send_email, _security_notice(
        user, "Senha alterada", f"A senha da sua conta foi {'alterada' if had_password else 'definida'} "
                                "em Configurações."))
    await db.refresh(user)
    return {"user": public_user(user), "ended_sessions": ended}


# ---------------------------------------------------------------------------
# SEGURANÇA (backlog: MFA): verificação em duas etapas
# ---------------------------------------------------------------------------
# Opcional (decisão de produto): o profissional ativa em Configurações >
# Minha conta, e o painel recomenda ativar enquanto estiver desligada. Vale
# para o login com SENHA; quem entra pelo Google usa a proteção da própria
# conta Google.
#
# Login com MFA, em dois passos:
#   1. POST /auth/login com e-mail e senha certos -> NÃO cria sessão; devolve
#      um `mfa_token` que vale 5 minutos e só serve para o passo 2;
#   2. POST /auth/login/mfa com o mfa_token + o código do aplicativo (ou um
#      código de recuperação) -> aí sim a sessão é criada.
MFA_LOGIN_MINUTES = 5
MFA_AUDIENCE = "psigestao-mfa"
MFA_INVALID_DETAIL = {"code": "mfa_invalid", "msg": "Código incorreto. Confira o aplicativo e tente de novo."}


def _mfa_login_token(user: User) -> str:
    issued_at = now_utc()
    return jwt.encode({
        "sub": user.user_id, "iat": issued_at, "exp": issued_at + timedelta(minutes=MFA_LOGIN_MINUTES),
        # Destinatário próprio: este token NUNCA é aceito como sessão (que exige
        # aud=psigestao-web e sid), e o de sessão nunca é aceito aqui.
        "iss": JWT_ISSUER, "aud": MFA_AUDIENCE, "jti": secrets.token_urlsafe(8),
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _mfa_login_user_id(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER,
                             audience=MFA_AUDIENCE, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail={
            "code": "mfa_expired", "msg": "O tempo para digitar o código acabou. Entre de novo com a senha."})
    return str(payload["sub"])


def _recovery_hashes(user: User) -> list:
    try:
        return json.loads(user.mfa_recovery_codes or "[]")
    except ValueError:
        return []


async def _check_mfa_code(db: AsyncSession, user: User, code: str) -> Optional[str]:
    """Confere o código do aplicativo ou um código de recuperação.

    Devolve "totp", "recovery" ou None. Os dois casos gravam de forma ATÔMICA
    (UPDATE com condição), para o mesmo código não valer duas vezes nem em
    requisições simultâneas.
    """
    if not user.mfa_secret or not code:
        return None
    step = totp.verify(decrypt_field(user.mfa_secret), code, user.mfa_last_step)
    if step is not None:
        result = await db.execute(
            update(User).where(User.user_id == user.user_id,
                               (User.mfa_last_step.is_(None)) | (User.mfa_last_step < step))
            .values(mfa_last_step=step).execution_options(synchronize_session=False)
        )
        await db.commit()
        if result.rowcount == 1:
            user.mfa_last_step = step
            return "totp"
        return None

    hashes = _recovery_hashes(user)
    candidate = totp.hash_recovery_code(code)
    if candidate in hashes:
        remaining = json.dumps([h for h in hashes if h != candidate])
        result = await db.execute(
            update(User).where(User.user_id == user.user_id, User.mfa_recovery_codes == user.mfa_recovery_codes)
            .values(mfa_recovery_codes=remaining).execution_options(synchronize_session=False)
        )
        await db.commit()
        if result.rowcount == 1:
            user.mfa_recovery_codes = remaining
            return "recovery"
    return None


@api_router.post("/auth/login/mfa", dependencies=[Depends(rate_limit("login-mfa", 10, 60))])
async def login_mfa(data: MfaLoginInput, background: BackgroundTasks, response: Response,
                    db: AsyncSession = Depends(get_db)):
    user_id = _mfa_login_user_id(data.mfa_token)
    # Força bruta dos 6 dígitos (quem já tem a senha): 5 tentativas a cada
    # 15 min e 20 por dia, por CONTA. Com ~3 códigos válidos por vez, a chance
    # de acertar por sorte fica perto de 0,006% ao dia.
    await enforce(f"mfa-user:{user_id}", 5, 900)
    await enforce(f"mfa-user-day:{user_id}", 20, 86400)
    user = await db.get(User, user_id)
    if user is None or user.closed_at is not None or user.mfa_enabled_at is None:
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")

    method = await _check_mfa_code(db, user, data.code)
    device = describe_user_agent(_audit_user_agent.get())
    if method is None:
        await log_audit(db, user.user_id, user.email, "entrar_falha", "sessao", user.user_id,
                        f"Entrada recusada: código de verificação incorreto ({device})")  # F3: em português
        raise HTTPException(status_code=401, detail=MFA_INVALID_DETAIL)

    set_auth_cookie(response, await open_login_session(db, user))
    detail = f"Entrada com senha e verificação em duas etapas ({device})"  # F3: em português
    left = len(_recovery_hashes(user))
    if method == "recovery":
        detail += f"; código de recuperação usado ({count_label(left, 'restante', 'restantes')})"
        background.add_task(mailer.send_email, _security_notice(
            user, "Código de recuperação usado",
            f"Um código de recuperação foi usado para entrar na sua conta. Restam {left}."))
    await log_audit(db, user.user_id, user.email, "entrar", "sessao", user.user_id, detail)
    return {"user": public_user(user)}


@api_router.post("/auth/mfa/setup")
async def mfa_setup(user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Começa a configuração: segredo novo + endereço do QR code. Só passa a
    valer depois de confirmado com um código (POST /auth/mfa/enable)."""
    if user.mfa_enabled_at is not None:
        raise HTTPException(status_code=409, detail="A verificação em duas etapas já está ativa.")
    if not user.password_hash:
        raise HTTPException(status_code=400, detail=(
            "Quem entra pelo Google usa a verificação da própria conta Google. Para ativar aqui, "
            "defina antes uma senha."))
    secret = totp.new_secret()
    user.mfa_secret = encrypt_field(secret)
    user.mfa_last_step = None
    await db.commit()
    return {"secret": secret, "otpauth_uri": totp.provisioning_uri(secret, user.email)}


@api_router.post("/auth/mfa/enable", dependencies=[Depends(rate_limit("mfa-manage", 10, 300))])
async def mfa_enable(data: MfaCodeInput, background: BackgroundTasks, user: User = Depends(get_active_user),
                     db: AsyncSession = Depends(get_db)):
    if user.mfa_enabled_at is not None:
        raise HTTPException(status_code=409, detail="A verificação em duas etapas já está ativa.")
    if not user.mfa_secret:
        raise HTTPException(status_code=400, detail="Comece a configuração de novo.")
    step = totp.verify(decrypt_field(user.mfa_secret), data.code)
    if step is None:
        raise HTTPException(status_code=400, detail=MFA_INVALID_DETAIL)

    codes = totp.new_recovery_codes()
    user.mfa_enabled_at = now_utc()
    user.mfa_last_step = step
    user.mfa_recovery_codes = json.dumps([totp.hash_recovery_code(c) for c in codes])
    await db.commit()
    await db.refresh(user)
    await log_audit(db, user.user_id, user.email, "mfa_ativar", "usuario", user.user_id,
                    "Verificação em duas etapas ativada")
    background.add_task(mailer.send_email, _security_notice(
        user, "Verificação em duas etapas ativada",
        "A verificação em duas etapas foi ativada. A partir de agora, o login com senha pede também o "
        "código do aplicativo autenticador."))
    # Os códigos de recuperação aparecem UMA vez (o banco só tem o hash).
    return {"user": public_user(user), "recovery_codes": codes}


@api_router.post("/auth/mfa/disable", dependencies=[Depends(rate_limit("mfa-manage", 10, 300))])
async def mfa_disable(data: MfaDisableInput, background: BackgroundTasks, user: User = Depends(get_active_user),
                      db: AsyncSession = Depends(get_db)):
    """Desligar exige a senha E um código: quem achar o computador logado
    não consegue desligar a proteção."""
    if user.mfa_enabled_at is None:
        raise HTTPException(status_code=400, detail="A verificação em duas etapas não está ativa.")
    await enforce(f"mfa-manage-user:{user.user_id}", 5, 900)
    if not user.password_hash or not await verify_password_async(data.password, user.password_hash):
        raise HTTPException(status_code=403, detail={"code": "invalid_current_password",
                                                     "msg": "A senha atual está incorreta."})
    if await _check_mfa_code(db, user, data.code) is None:
        raise HTTPException(status_code=403, detail=MFA_INVALID_DETAIL)

    user.mfa_secret = None
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    user.mfa_recovery_codes = None
    await db.commit()
    await db.refresh(user)
    await log_audit(db, user.user_id, user.email, "mfa_desativar", "usuario", user.user_id,
                    "Verificação em duas etapas desativada")
    background.add_task(mailer.send_email, _security_notice(
        user, "Verificação em duas etapas desativada",
        "A verificação em duas etapas foi desativada. O login volta a pedir só a senha."))
    return {"user": public_user(user)}


@api_router.post("/auth/mfa/recovery-codes", dependencies=[Depends(rate_limit("mfa-manage", 10, 300))])
async def mfa_new_recovery_codes(data: MfaCodeInput, user: User = Depends(get_active_user),
                                 db: AsyncSession = Depends(get_db)):
    """Gera códigos de recuperação novos (os anteriores deixam de valer)."""
    if user.mfa_enabled_at is None:
        raise HTTPException(status_code=400, detail="A verificação em duas etapas não está ativa.")
    await enforce(f"mfa-manage-user:{user.user_id}", 5, 900)
    if await _check_mfa_code(db, user, data.code) != "totp":
        # Só o código do APLICATIVO: um código de recuperação não gera outros.
        raise HTTPException(status_code=403, detail=MFA_INVALID_DETAIL)
    codes = totp.new_recovery_codes()
    user.mfa_recovery_codes = json.dumps([totp.hash_recovery_code(c) for c in codes])
    await db.commit()
    await db.refresh(user)
    await log_audit(db, user.user_id, user.email, "mfa_codigos", "usuario", user.user_id,
                    "Novos códigos de recuperação gerados (os anteriores foram invalidados)")
    return {"user": public_user(user), "recovery_codes": codes}


# ---------------------------------------------------------------------------
# SEGURANÇA MÉDIA: sessões com aparelho
# ---------------------------------------------------------------------------
# Antes a tela mostrava só QUANTAS outras sessões existiam. Agora lista cada
# uma com aparelho, IP e último uso, e permite encerrar uma de cada vez.

@api_router.get("/auth/sessions")
async def list_login_sessions(sess: UserSession = Depends(get_current_session), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(UserSession).where(
        UserSession.user_id == sess.user_id, UserSession.expires_at > now_utc(),
    ))).scalars().all()
    items = [{
        "id": s.public_id,
        "device": s.device or "Aparelho não identificado",
        "ip_address": s.ip_address or "",
        "created_at": _iso(s.created_at),
        "last_activity_at": _iso(s.last_activity_at or s.created_at),
        "current": s.token_hash == sess.token_hash,
    } for s in rows]
    # Esta sessão primeiro; depois a usada mais recentemente.
    items = [i for i in items if i["current"]] + sorted(
        [i for i in items if not i["current"]], key=lambda i: i["last_activity_at"], reverse=True)
    # `other_sessions`: mantido para quem ainda lê só a contagem.
    return {"other_sessions": sum(1 for i in items if not i["current"]), "sessions": items}


@api_router.post("/auth/sessions/{session_id}/end")
async def end_one_session(data: EndSessionsInput, session_id: str = PathParam(max_length=64),
                          sess: UserSession = Depends(get_current_session), db: AsyncSession = Depends(get_db)):
    """Encerra UMA sessão de outro aparelho (ex.: o computador da clínica)."""
    if session_id == sess.public_id:
        raise HTTPException(status_code=400, detail="Para sair deste aparelho, use o botão Sair.")
    target = (await db.execute(select(UserSession).where(
        UserSession.user_id == sess.user_id, UserSession.public_id == session_id,
    ))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada (talvez já tenha sido encerrada).")
    device = target.device or "aparelho não identificado"
    await db.delete(target)
    await db.commit()
    user = sess.user
    await log_audit(db, user.user_id, user.email, "encerrar", "sessao", user.user_id,
                    f"Sessão encerrada em outro aparelho ({device})")
    return {"ok": True}


@api_router.post("/auth/sessions/end-others")
async def end_other_sessions(data: EndSessionsInput, sess: UserSession = Depends(get_current_session),
                             db: AsyncSession = Depends(get_db)):
    """"Sair dos outros dispositivos": encerra todas as sessões menos esta."""
    user = sess.user
    ended = await end_login_sessions(db, user, keep=sess)
    await log_audit(db, user.user_id, user.email, "encerrar", "sessao", user.user_id,
                    f"Sair dos outros dispositivos: {count_label(ended, 'sessão encerrada', 'sessões encerradas')}")
    return {"ended_sessions": ended}


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
    unchanged = (user.consent_term_body or None) == (None if not body or body == DEFAULT_CONSENT_TERM_BODY else body)
    user.consent_term_body = None if not body or body == DEFAULT_CONSENT_TERM_BODY else body
    # Análise de UI (O1): salvar (mesmo sem mudar nada, "Revisei e mantenho este
    # texto") conta como revisão do termo, um dos "Primeiros passos" do painel.
    user.consent_term_reviewed_at = now_utc()
    await db.commit()
    await db.refresh(user)

    # Mudar o modelo NÃO altera consentimentos já dados: cada paciente guarda
    # a cópia do texto que aceitou (ver apply_consent).
    detail = "Modelo do termo de consentimento"
    if unchanged:
        detail += " (revisado, sem alteração)"
    elif user.consent_term_body is None:
        detail += " (padrão restaurado)"
    await log_audit(db, user.user_id, user.email, "editar", "termo", user.user_id, detail)
    return await get_consent_term(user)

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


def _bump_last_consultation(p: Patient, moment: Optional[datetime]) -> None:
    """Atualiza a "última consulta" do paciente, mas SÓ para frente.

    CORREÇÃO (varredura de UI): antes era sobrescrita pela data da evolução
    registrada por último. Registrar hoje a evolução de uma sessão de semanas
    atrás fazia a "última consulta" voltar no tempo. E uma sessão marcada como
    realizada na agenda não contava. Agora conta a mais recente das duas.
    """
    moment = _as_utc(moment)
    if moment is None:
        return
    try:
        current = _as_utc(datetime.fromisoformat(p.last_consultation_date)) if p.last_consultation_date else None
    except ValueError:
        current = None
    if current is None or moment > current:
        p.last_consultation_date = moment.isoformat()


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
# Índice cego de busca por nome (ESCALA), telefone e CPF (análise de UI, P5)
# ---------------------------------------------------------------------------
def search_contacts(data) -> dict:
    """P5: telefones e CPFs (do paciente e do responsável) que entram no índice."""
    return {
        "phones": (data.phone, getattr(data, "guardian_phone", "")),
        "cpfs": (data.cpf, getattr(data, "guardian_cpf", "")),
    }


async def _index_patient_search(db: AsyncSession, p: Patient, plain_name: str, *, phones=(), cpfs=()) -> None:
    """(Re)grava os tokens de busca do paciente. Chamado em toda criação,
    edição, anonimização e cadastro pelo link do paciente, na MESMA transação da
    alteração: o índice nunca fica desatualizado em relação ao nome.
    P5: inclui telefone e CPF (antes só o nome era buscável)."""
    await db.flush()  # garante o id de um paciente recém-criado
    await db.execute(delete(PatientSearchToken).where(PatientSearchToken.patient_id == p.id))
    tokens = name_search_tokens(_search_key, p.owner_id, plain_name)
    tokens |= contact_search_tokens(_search_key, p.owner_id, phones, cpfs)
    db.add_all([PatientSearchToken(patient_id=p.id, owner_id=p.owner_id, token=token) for token in tokens])
    p.search_index_version = SEARCH_INDEX_VERSION


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


# Segurança (varredura, item 3): um cursor de paginação real tem ~60 caracteres.
CURSOR_MAX = 200


# Complementares (lista de pacientes): ordem da lista.
#   - "recent": cadastrados mais recentemente primeiro (padrão, como antes);
#   - "name": nome de A a Z (sem diferenciar acentos e maiúsculas);
#   - "last_consultation": última consulta mais recente primeiro; quem nunca
#     consultou fica no fim.
# O nome é CRIPTOGRAFADO no banco (item 13), então o banco não consegue
# ordená-lo. Nas ordens "name" e "last_consultation" a lista do profissional é
# lida, descriptografada e ordenada aqui, e a página sai dessa lista (cursor =
# posição). Mesmo com milhares de pacientes leva milissegundos; a ordem padrão
# continua toda no banco, com o cursor por (data, id).
PatientSort = Literal["recent", "name", "last_consultation"]


class PatientSearchInput(BaseModel):
    q: str = Field(default="", max_length=120)
    archived: bool = False
    cursor: Optional[str] = Field(default=None, max_length=CURSOR_MAX)
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    sort: PatientSort = "recent"


def _encode_offset_cursor(offset: int) -> str:
    raw = json.dumps(["offset", offset]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_offset_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        kind, offset = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if kind != "offset" or not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except Exception:
        raise HTTPException(status_code=400, detail="Cursor de paginação inválido.")


def _name_sort_key(name: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").casefold()


async def _next_sessions(db: AsyncSession, user: User, patient_ids: list) -> dict:
    """Complementares: próxima sessão marcada (não cancelada) de cada paciente
    da página, numa consulta só (pelo índice ix_sessions_owner_start)."""
    if not patient_ids:
        return {}
    rows = await db.execute(
        select(Session.patient_id, func.min(Session.start_time)).where(
            Session.owner_id == user.user_id, Session.patient_id.in_(patient_ids),
            Session.start_time >= now_utc(), _occupies_slot,  # C4: nem cancelada nem falta
        ).group_by(Session.patient_id)
    )
    return {pid: _iso(_as_utc(start)) for pid, start in rows.all()}


async def _patients_page(db: AsyncSession, user: User, *, archived: bool, query: str,
                         cursor: Optional[str], limit: int, sort: str = "recent") -> dict:
    # Item 12: arquivados saem da lista ativa, mas continuam acessíveis por filtro.
    conditions = [Patient.owner_id == user.user_id,
                  Patient.archived_at.is_not(None) if archived else Patient.archived_at.is_(None)]

    # P5: a busca é um telefone ou CPF ("(11) 91234-5678", "123.456.789-09")?
    # Então basta UM dos tokens bater (o mesmo número pode ser telefone ou CPF).
    contact_tokens = query_contact_tokens(_search_key, user.user_id, query)
    if contact_tokens:
        conditions.append(Patient.id.in_(
            select(PatientSearchToken.patient_id).where(
                PatientSearchToken.owner_id == user.user_id, PatientSearchToken.token.in_(contact_tokens),
            )
        ))
    # Nome: cada palavra digitada vira um token; o paciente precisa ter TODOS.
    # Palavras de 1 letra são ignoradas (não há token para elas).
    for token in ([] if contact_tokens else query_search_tokens(_search_key, user.user_id, query)):
        conditions.append(Patient.id.in_(
            select(PatientSearchToken.patient_id).where(
                PatientSearchToken.owner_id == user.user_id, PatientSearchToken.token == token,
            )
        ))

    # Complementares: total de pacientes (com os mesmos filtros), para a tela.
    total = await db.scalar(select(func.count()).select_from(Patient).where(*conditions))

    if sort == "recent":
        stmt = select(Patient).where(*conditions)
        if cursor:
            moment, row_id = decode_cursor(cursor)
            stmt = stmt.where(tuple_(Patient.created_at, Patient.id) < (moment, row_id))
        stmt = stmt.order_by(desc(Patient.created_at), desc(Patient.id)).limit(limit + 1)
        rows = (await db.execute(stmt)).scalars().all()
        rows, next_cursor = _split_page(rows, limit, lambda p: p.created_at)
        items = [patient_summary(p) for p in rows]
    else:
        offset = _decode_offset_cursor(cursor) if cursor else 0
        everything = [patient_summary(p) for p in (await db.execute(select(Patient).where(*conditions))).scalars().all()]
        everything.sort(key=lambda item: _name_sort_key(item["full_name"]))
        if sort == "last_consultation":
            # Ordenação estável: entre datas iguais, continua por nome. Sem
            # consulta ("") fica por último na ordem decrescente.
            everything.sort(key=lambda item: item["last_consultation_date"] or "", reverse=True)
        items = everything[offset:offset + limit]
        next_cursor = _encode_offset_cursor(offset + limit) if offset + limit < len(everything) else None

    upcoming = await _next_sessions(db, user, [item["id"] for item in items])
    for item in items:
        item["next_session"] = upcoming.get(item["id"], "")
    return {"items": items, "next_cursor": next_cursor, "total": total}

# ---------------------------------------------------------------------------
# Rotas de Pacientes e Prontuários
# ---------------------------------------------------------------------------

@api_router.get("/patients")
async def list_patients(
    archived: bool = Query(False, description="true = lista só os arquivados (item 12)"),
    cursor: Optional[str] = Query(None, max_length=CURSOR_MAX, description="next_cursor da página anterior"),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    # Complementares (lista de pacientes): ordem escolhida na tela.
    sort: PatientSort = Query("recent"),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    # ESCALA: paginada e com itens enxutos (patient_summary).
    # A listagem não gera log de "visualizar" por paciente (seria um registro
    # por linha a cada abertura da tela); o acesso à ficha e ao prontuário sim.
    return await _patients_page(db, user, archived=archived, query="", cursor=cursor, limit=limit, sort=sort)

# SEGURANÇA (varredura, item 15): limites de busca e exportação.
# Antes nenhuma das duas tinha limite. Com uma sessão roubada (ou um script),
# dava para varrer a lista de pacientes por busca e exportar o prontuário
# COMPLETO de todos em segundos; e cada PDF pesa na CPU do servidor.
# Os limites contam por IP (dependência) e por CONTA (dentro da rota), para
# um cookie roubado usado de vários IPs não multiplicar a cota.
# A busca já espera 300 ms sem digitação antes de consultar: 90 por minuto
# sobra para o uso real.
SEARCH_LIMIT_PER_MINUTE = 90
EXPORT_LIMIT, EXPORT_WINDOW_SECONDS = 10, 300  # 10 exportações a cada 5 minutos


@api_router.post("/patients/search", dependencies=[Depends(rate_limit("search", 2 * SEARCH_LIMIT_PER_MINUTE, 60))])
async def search_patients(data: PatientSearchInput, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Busca por nome, no servidor, pelo índice cego (ESCALA). Desde a análise
    de UI (P5), também por telefone (com ou sem DDD) e por CPF.

    É POST, e não GET com ?q=, DE PROPÓSITO: o termo buscado é o nome (ou o
    telefone, o CPF) de um paciente. Em GET ele iria para a URL, e URLs ficam gravadas em logs de
    proxy, de load balancer, de CDN e no histórico do navegador. No corpo
    de um POST ele não é registrado por nenhuma dessas camadas.
    """
    await enforce(f"search-user:{user.user_id}", SEARCH_LIMIT_PER_MINUTE, 60)  # varredura, item 15
    return await _patients_page(db, user, archived=data.archived, query=data.q,
                                cursor=data.cursor, limit=data.limit, sort=data.sort)

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

async def _new_patient(db: AsyncSession, owner: User, data: PatientInput) -> tuple:
    """Cria o paciente na sessão (sem commit): campos criptografados, termo de
    consentimento com evidência e índice de busca. Um só lugar para o cadastro
    manual, o envio do paciente pelo link e o cadastro incompleto completado pela
    psicóloga: esquecer uma dessas etapas num dos caminhos gravaria dado em claro
    ou deixaria o paciente fora da busca. Devolve (paciente, ação do consentimento)."""
    patient = Patient(owner_id=owner.user_id, anonymized=False)
    _apply_patient_input(patient, data)
    consent_action = apply_consent(patient, data.consent_terms, owner)
    db.add(patient)
    # Escala: busca no servidor. P5: também por telefone e CPF.
    await _index_patient_search(db, patient, data.full_name, **search_contacts(data))
    return patient, consent_action


@api_router.post("/patients")
async def create_patient(data: PatientInput, user: User = Depends(get_writer), db: AsyncSession = Depends(get_db)):
    # Produto (assinatura): paciente NOVO exige gravação (get_writer). Corrigir o
    # cadastro de quem já existe (PUT) continua liberado: é direito do paciente
    # (LGPD art. 18, III) e serve também para revogar o consentimento.
    novo_paciente, consent_action = await _new_patient(db, user, data)
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
    # Escala: o nome pode ter mudado. P5: e o telefone e o CPF também.
    await _index_patient_search(db, p, data.full_name, **search_contacts(data))

    await db.commit()
    await db.refresh(p)

    await log_audit(db, user.user_id, user.email, "editar", "paciente", p.id, "Edição de cadastro")
    if consent_action:
        detail = "Termo de consentimento registrado" if consent_action == "consentir" else "Consentimento revogado"
        await log_audit(db, user.user_id, user.email, consent_action, "paciente", p.id, detail)
    return patient_public(p, user)

def _record_conflict(current_version: int) -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": "record_conflict",
        "msg": (f"Esta anotação foi alterada em outro lugar enquanto você editava (agora está na versão "
                f"{current_version}). Nada foi salvo."),
        "current_version": current_version,
    })


@api_router.put("/records/{rid}")
async def update_record(rid: str, data: RecordUpdateInput, user: User = Depends(get_writer),  # assinatura: grava
                        db: AsyncSession = Depends(get_db)):
    """Edita uma evolução, arquivando a versão anterior.

    SEGURANÇA (varredura, item 9): CONTROLE OTIMISTA DE VERSÃO.
    Antes: a rota lia a versão, arquivava e gravava sem trava nenhuma. Se a
    mesma anotação fosse salva ao mesmo tempo em duas abas ou dois aparelhos,
    as duas liam a versão N; uma sobrescrevia a outra, e o texto perdido NEM
    entrava no histórico (as duas arquivavam a mesma versão N).
    Agora:
      1. o frontend informa a versão que abriu para editar (expected_version);
      2. se o registro já está em outra versão, a resposta é 409 e nada muda;
      3. a gravação é um UPDATE condicional ("... WHERE version = N"): se outra
         edição passar na frente entre a leitura e a gravação, o banco não
         atualiza nenhuma linha, tudo é desfeito (inclusive a versão arquivada)
         e a resposta também é 409.
    No 409 o frontend oferece "Salvar mesmo assim": a edição que chegou antes
    vira versão do histórico, e nenhum texto se perde.
    """
    stmt = select(Record).where(Record.id == rid, Record.owner_id == user.user_id)
    result = await db.execute(stmt)
    r = result.scalar_one_or_none()

    if not r:
        # Terminologia (UX): prontuário é o do paciente; cada registro de sessão é uma EVOLUÇÃO.
        raise HTTPException(status_code=404, detail="Evolução não encontrada")
    if r.version != data.expected_version:
        raise _record_conflict(r.version)

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

    # Atualizando o registro principal, SÓ se ele ainda estiver na versão lida.
    updated = await db.execute(
        update(Record)
        .where(Record.id == r.id, Record.version == data.expected_version)
        .values(
            session_datetime=data.session_datetime,
            content=encrypt_field(data.content),
            diagnosis=encrypt_field(data.diagnosis),
            version=Record.version + 1,
            updated_at=now_utc(),
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        await db.rollback()  # desfaz também a versão arquivada acima
        current = await db.scalar(select(Record.version).where(Record.id == rid))
        raise _record_conflict(current or data.expected_version)

    await db.commit()
    await db.refresh(r)

    # Terminologia (UX): cada registro de sessão é uma "evolução" em toda a tela.
    await log_audit(db, user.user_id, user.email, "editar", "prontuario", r.id, f"Evolução editada (versão {r.version})")
    return record_public(r)

@api_router.get("/records/{rid}/versions")
async def record_versions(rid: str, user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Histórico de versões de uma evolução (UX: antes as versões eram guardadas
    a cada edição, mas não havia como vê-las na tela).

    Devolve a versão atual e as anteriores, da mais nova para a mais antiga,
    com a data em que cada uma foi substituída. A consulta entra na trilha de
    auditoria, como qualquer leitura de prontuário.
    """
    r = (await db.execute(select(Record).where(Record.id == rid, Record.owner_id == user.user_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Evolução não encontrada")
    stmt = select(RecordVersion).where(
        RecordVersion.record_id == rid, RecordVersion.owner_id == user.user_id,
    ).order_by(desc(RecordVersion.version))
    versions = (await db.execute(stmt)).scalars().all()
    await log_audit(db, user.user_id, user.email, "visualizar", "prontuario", rid,
                    f"Histórico de versões da evolução ({count_label(len(versions) + 1, 'versão', 'versões')})")
    return {
        "current": record_public(r),
        "versions": [{
            "version": v.version,
            "session_datetime": _iso(v.session_datetime),
            "content": decrypt_field(v.content),
            "diagnosis": decrypt_field(v.diagnosis),
            "replaced_at": _iso(v.archived_at),
        } for v in versions],
    }


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
    # Escala/LGPD: os tokens do nome real (e do telefone e CPF, P5) saem do
    # índice de busca junto: sem contatos, só o marcador fica buscável.
    await _index_patient_search(db, p, "Paciente Anonimizado")
    p.cpf = ""
    p.rg = ""
    p.address = ""
    p.phone = ""
    p.email = ""
    p.emergency_contact = ""
    p.initial_notes = ""
    # LGPD (correção encontrada na análise de UI, P5): os dados do RESPONSÁVEL
    # legal (fluxo clínico, C3) também identificam o paciente e ficavam depois
    # da anonimização. Agora saem junto.
    p.guardian_name = ""
    p.guardian_cpf = ""
    p.guardian_relationship = ""
    p.guardian_phone = ""
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
    cursor: Optional[str] = Query(None, max_length=CURSOR_MAX, description="next_cursor da página anterior"),
    limit: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    # Complementares (evoluções): só as sessões do período (início incluído, fim excluído).
    start: Optional[datetime] = Query(None, description="sessões a partir de (ISO com fuso)"),
    end: Optional[datetime] = Query(None, description="sessões antes de (ISO com fuso)"),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    # Verifica se o paciente existe e pertence ao usuário
    await _get_owned_patient(db, pid, user)

    # ESCALA: paginada (a sessão mais recente primeiro). Um paciente de anos
    # tem centenas de evoluções; antes TODAS eram descriptografadas e enviadas
    # a cada abertura da ficha. Usa o índice ix_records_patient_session.
    # session_datetime é sempre preenchido pela API (RecordInput o exige).
    base = [Record.patient_id == pid, Record.owner_id == user.user_id]
    if start is not None:
        base.append(Record.session_datetime >= _as_utc(start))
    if end is not None:
        base.append(Record.session_datetime < _as_utc(end))
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
    period = "; filtradas por período" if start is not None or end is not None else ""
    await log_audit(db, user.user_id, user.email, "visualizar", "prontuario", pid,
                    f"Lista de evoluções ({count_label(len(records), 'evolução', 'evoluções')}{period})")
    return {"items": records, "next_cursor": next_cursor, "total": total}

@api_router.post("/patients/{pid}/records")
async def create_record(pid: str, data: RecordInput, user: User = Depends(get_writer),  # assinatura: grava
                        db: AsyncSession = Depends(get_db)):
    p = await _get_owned_patient(db, pid, user)

    # PRODUTO (agenda -> evolução): evolução registrada a partir de uma sessão.
    # A sessão precisa ser deste profissional E deste paciente; sessão cancelada
    # não recebe evolução; e cada sessão tem no máximo uma (para editar, abre-se
    # a existente). Registrar a evolução confirma que a sessão aconteceu: ela
    # passa a "realizada" na agenda.
    session = None
    if data.session_id:
        session = (await db.execute(select(Session).where(
            Session.id == data.session_id, Session.owner_id == user.user_id,
        ))).scalar_one_or_none()
        if session is None or session.patient_id != pid:
            raise HTTPException(status_code=422, detail="Sessão da agenda não encontrada para este paciente.")
        if (session.status or "agendada") == "cancelada":
            raise HTTPException(status_code=422, detail="Uma sessão cancelada não recebe evolução.")
        # Fluxo clínico (C4): ligar a evolução marcaria a sessão como realizada,
        # contradizendo a falta. Uma observação sobre a falta pode ser registrada
        # na ficha, como evolução avulsa.
        if session.status == "faltou":
            raise HTTPException(status_code=422, detail=(
                "Sessão marcada como falta. Para registrar uma observação, use “Nova evolução” na ficha do paciente."))
        existing = await db.scalar(select(Record.id).where(Record.session_id == session.id))
        if existing:
            raise HTTPException(status_code=409, detail={
                "code": "session_has_record", "record_id": existing,
                "msg": "Esta sessão já tem uma evolução registrada. Abra-a para editar.",
            })
        session.status = "realizada"

    novo_prontuario = Record(
        owner_id=user.user_id,
        patient_id=pid,
        session_datetime=data.session_datetime, # DateTime nativo do Pydantic
        content=encrypt_field(data.content),
        diagnosis=encrypt_field(data.diagnosis),
        version=1,
        session_id=session.id if session else None,
    )

    # Atualiza a data da última consulta no paciente (só para frente; ver a função).
    _bump_last_consultation(p, data.session_datetime)

    db.add(novo_prontuario)
    await db.commit()
    await db.refresh(novo_prontuario)

    # A data fica em ISO/UTC (sem ambiguidade de fuso); a tela de auditoria a
    # converte para a hora local ao exibir (Settings.js, formatAuditDetail).
    # Terminologia (UX): "Evolução registrada", e se veio de uma sessão da agenda.
    origin = " (sessão da agenda)" if data.session_id else ""
    await log_audit(db, user.user_id, user.email, "criar", "prontuario", novo_prontuario.id,
                    f"Evolução registrada · sessão de {data.session_datetime.isoformat()}{origin}")
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
    # Fluxo clínico (C3): responsável legal de paciente menor de idade.
    if pat.get("guardian_name"):
        relationship = f" ({pdf_text(pat['guardian_relationship'])})" if pat.get("guardian_relationship") else ""
        elems.append(Paragraph(
            f"<b>Responsável legal:</b> {pdf_text(pat['guardian_name'])}{relationship} · "
            f"CPF {pdf_text(pat.get('guardian_cpf') or '—')} · Telefone {pdf_text(pat.get('guardian_phone') or '—')}", body))
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


@api_router.get("/patients/{pid}/export",
                dependencies=[Depends(rate_limit("export", 2 * EXPORT_LIMIT, EXPORT_WINDOW_SECONDS))])
async def export_patient(pid: str,
                         # Segurança (varredura, item 3): antes qualquer texto era aceito, ia
                         # para a auditoria e caía no PDF. Agora só os dois formatos existentes.
                         format: Literal["json", "pdf"] = Query("json"),
                         user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    # Varredura, item 15: limite por conta (ver SEARCH_LIMIT_PER_MINUTE acima).
    await enforce(f"export-user:{user.user_id}", EXPORT_LIMIT, EXPORT_WINDOW_SECONDS)
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

# Complementares (auditoria): filtros por período, ação e tipo, e exportação.
# Antes a trilha só podia ser rolada, do mais recente para trás: achar "quem
# acessou este prontuário em março" exigia carregar página por página.
AUDIT_EXPORT_MAX_ROWS = 50_000
# Rótulos da exportação (os mesmos da tela, frontend/src/pages/Settings.js).
AUDIT_ACTION_LABELS = {
    "criar": "Criação", "editar": "Edição", "visualizar": "Visualização", "excluir": "Exclusão",
    "anonimizar": "Anonimização", "exportar": "Exportação", "arquivar": "Arquivamento",
    "desarquivar": "Reativação", "consentir": "Consentimento", "revogar": "Revogação", "aceitar": "Aceite",
    "gerar_token": "Novo token", "bloquear": "Bloqueio", "desbloquear": "Desbloqueio",
    "desbloquear_falha": "Senha incorreta", "encerrar": "Encerramento", "vincular": "Vínculo Google",
    # Análise de UI (F3): "Entrada"/"Saída" em vez de "Login"/"Logout" (os mesmos rótulos da tela).
    "alterar_senha": "Troca de senha", "alterar_senha_falha": "Senha incorreta", "entrar": "Entrada",
    "entrar_falha": "Entrada recusada", "sair": "Saída", "solicitar_senha": "Pedido de nova senha",
    "mfa_ativar": "Duas etapas ativada", "mfa_desativar": "Duas etapas desativada",
    "mfa_codigos": "Novos códigos",
}
AUDIT_ENTITY_LABELS = {
    "paciente": "Paciente", "prontuario": "Prontuário", "agenda": "Agenda", "usuario": "Usuário",
    "termos": "Política", "termo": "Termo", "integracao": "Integração", "sessao": "Sessão",
    "auditoria": "Auditoria",
}


def _audit_filters(user: User, action: Optional[str], entity: Optional[str],
                   start: Optional[datetime], end: Optional[datetime]) -> list:
    conditions = [AuditLog.owner_id == user.user_id]
    if action:
        conditions.append(AuditLog.action == action)
    if entity:
        conditions.append(AuditLog.entity_type == entity)
    if start is not None:
        conditions.append(AuditLog.timestamp >= _as_utc(start))
    if end is not None:
        conditions.append(AuditLog.timestamp < _as_utc(end))  # fim exclusivo
    return conditions


@api_router.get("/audit")
async def get_audit(
    cursor: Optional[str] = Query(None, max_length=CURSOR_MAX, description="next_cursor da página anterior"),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    action: Optional[str] = Query(None, max_length=40, description="só esta ação (ex.: entrar)"),
    entity: Optional[str] = Query(None, max_length=40, description="só este tipo (ex.: prontuario)"),
    start: Optional[datetime] = Query(None, description="a partir de (ISO com fuso)"),
    end: Optional[datetime] = Query(None, description="antes de (ISO com fuso)"),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    # ESCALA: antes mostrava só os 100 últimos registros, sem como ver os
    # anteriores (a trilha só cresce: inclui leituras). Agora é paginada pelo
    # índice ix_audit_logs_owner_timestamp.
    stmt = select(AuditLog).where(*_audit_filters(user, action, entity, start, end))
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


def _csv_cell(value) -> str:
    """Planilhas executam como FÓRMULA o que começa com = + - @ ("CSV
    injection"). Um texto da trilha nunca deve virar fórmula ao abrir o arquivo."""
    text_value = str(value or "")
    return "'" + text_value if text_value[:1] in ("=", "+", "-", "@", "\t", "\r") else text_value


_ISO_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})")


def _brasilia_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Sao_Paulo")
    except Exception:  # sem base de fusos no sistema: fica em UTC
        return None


@api_router.get("/audit/export", dependencies=[Depends(rate_limit("audit-export", 10, 300))])
async def export_audit(
    action: Optional[str] = Query(None, max_length=40),
    entity: Optional[str] = Query(None, max_length=40),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    """A trilha (com os mesmos filtros da tela) em CSV, para guardar ou
    entregar numa fiscalização. Separador ";" e BOM: abre certo no Excel em português."""
    rows = (await db.execute(
        select(AuditLog).where(*_audit_filters(user, action, entity, start, end))
        .order_by(desc(AuditLog.timestamp), desc(AuditLog.log_id)).limit(AUDIT_EXPORT_MAX_ROWS)
    )).scalars().all()
    tz = _brasilia_tz()

    def local(moment) -> str:
        moment = _as_utc(moment)
        return (moment.astimezone(tz) if tz else moment).strftime("%d/%m/%Y %H:%M:%S") if moment else ""

    def build() -> bytes:
        out = io.StringIO()
        writer = csv.writer(out, delimiter=";")
        writer.writerow([f"Data e hora ({'horário de Brasília' if tz else 'UTC'})", "Ação", "Tipo",
                         "Identificador", "Detalhe", "IP", "Navegador"])
        for a in rows:
            detail = _ISO_INSTANT.sub(lambda m: local(datetime.fromisoformat(m.group(0).replace("Z", "+00:00"))),
                                      a.detail or "")
            writer.writerow([_csv_cell(v) for v in (
                local(a.timestamp), AUDIT_ACTION_LABELS.get(a.action, a.action),
                AUDIT_ENTITY_LABELS.get(a.entity_type, a.entity_type), a.entity_id, detail,
                a.ip_address, a.user_agent)])
        return ("﻿" + out.getvalue()).encode("utf-8")

    data = await run_in_threadpool(build)
    await log_audit(db, user.user_id, user.email, "exportar", "auditoria", user.user_id,
                    f"Trilha de auditoria exportada ({count_label(len(rows), 'registro', 'registros')})")
    return StreamingResponse(io.BytesIO(data), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="trilha_auditoria_{now_utc():%Y%m%d}.csv"'})


# ---------------------------------------------------------------------------
# PRODUTO: cadastro pelo próprio paciente (link temporário)
# ---------------------------------------------------------------------------
# Substitui a integração com o Google Forms, REMOVIDA: lá os dados do paciente
# passavam pelo Google, o token valia por um ano dentro de um script de
# terceiro e a ficha chegava sem validação nenhuma.
#
# Fluxo:
#   1. a psicóloga gera o link (POST /intake-links). Vale 30 minutos para
#      COMEÇAR, para uma pessoa só. O banco guarda só o hash; o link vai no
#      fragmento (/cadastro#token=...), que não aparece em logs nem na prévia
#      automática do WhatsApp;
#   2. o paciente abre a página (POST /intake/open: mostra quem recebe os dados
#      e as regras; NÃO começa o tempo) e clica em "Começar" (POST /intake/start):
#      10 minutos, e o link fica PRESO ao navegador dele por um segredo próprio
#      (fill_secret, guardado só como hash). Se o link vazar depois disso,
#      ninguém continua de outro aparelho;
#   3. enquanto preenche, o que ele digita é salvo CRIPTOGRAFADO (PUT /intake/draft);
#      recarregar a página ou cair a internet retoma de onde parou (POST /intake/resume);
#   4. faltando até 3 minutos, ele pode pedir +10 minutos, até 10 vezes
#      (acessibilidade, WCAG 2.2.1: quem precisa de mais tempo não fica de fora);
#   5. enviou (POST /intake/submit): as MESMAS regras do cadastro manual (menos
#      "Notas iniciais", da psicóloga) e vira paciente. Reenviar (clique duplo,
#      rede caindo na resposta) não duplica: só UM envio reserva o link;
#   6. o tempo acabou pela metade: a psicóloga vê o cadastro como INCOMPLETO,
#      revisa e completa com o paciente (POST /intake-links/{id}/complete).
#      Nada do que o paciente digitou se perde.
# Segurança das rotas do paciente (públicas, sem login):
#   - token e segredo vão no CORPO (POST/PUT), nunca na URL;
#   - link desconhecido, cancelado ou vencido recebe a MESMA resposta (410),
#     sem revelar qual é o caso (não dá para "sondar" links);
#   - limite de tentativas por IP e por link, tamanho de cada campo limitado,
#     proteção CSRF (cabeçalho próprio) e limite de tamanho do corpo valem também aqui;
#   - o IP de quem enviou fica na trilha de auditoria da psicóloga: é a
#     evidência do consentimento (LGPD art. 8º, §2º), e a página avisa isso;
#   - o envio é aceito mesmo que a conta da psicóloga tenha entrado no modo
#     somente leitura depois de gerar o link: o paciente já fez a parte dele, e
#     os dados não podem se perder.
INTAKE_START_MINUTES = 30
INTAKE_FILL_MINUTES = 10
INTAKE_EXTENSION_MINUTES = 10
INTAKE_MAX_EXTENSIONS = 10           # WCAG 2.2.1: prorrogar ao menos 10 vezes
INTAKE_EXTEND_WINDOW_SECONDS = 180   # pedir mais tempo vale nos últimos 3 minutos
INTAKE_GRACE_SECONDS = 15            # atraso da rede no último salvamento/envio
INTAKE_MAX_PENDING = 20              # links ainda não começados, por psicóloga
INTAKE_KEEP_DAYS = 30                # depois disso, links encerrados saem da lista (manutenção)
INTAKE_ACTOR = "paciente (link de cadastro)"
INTAKE_INVALID = {"code": "intake_invalid",
                  "msg": "Este link de cadastro não é válido ou já expirou. Peça um novo link à sua psicóloga."}
INTAKE_DONE = {"code": "intake_done",
               "msg": "Este cadastro já foi enviado. Obrigado! Se precisar corrigir algo, fale com a sua psicóloga."}
INTAKE_OTHER_DEVICE = {"code": "intake_other_device",
                       "msg": "Este cadastro foi começado em outro aparelho ou navegador. Continue nele, "
                              "ou peça um novo link à sua psicóloga."}


class IntakeLinkInput(BaseModel):
    # "Para quem é o link" (opcional): só a psicóloga vê; gravado criptografado.
    label: str = Field(default="", max_length=60)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        value = (value or "").strip()
        if "\x00" in value:
            raise ValueError("Texto com caractere inválido.")
        return value


class IntakeTokenInput(BaseModel):
    token: str = Field(min_length=20, max_length=128)


class IntakeFillInput(IntakeTokenInput):
    fill_secret: str = Field(min_length=20, max_length=128)


class IntakeDraftInput(IntakeFillInput):
    data: IntakeDraftData


class IntakeSubmitInput(IntakeFillInput):
    data: IntakeSubmitData


def intake_status(intake: PatientIntake, now: Optional[datetime] = None) -> str:
    """pending | in_progress | submitted | completed | incomplete | expired | revoked."""
    now = now or now_utc()
    if intake.revoked_at:
        return "revoked"
    if intake.submitted_at:
        return "submitted" if intake.completed_by == "patient" else "completed"
    if intake.started_at:
        if now < _as_utc(intake.deadline_at):
            return "in_progress"
        return "incomplete" if intake.draft else "expired"
    return "pending" if now < _as_utc(intake.expires_at) else "expired"


def _intake_public(intake: PatientIntake) -> dict:
    """O que a psicóloga vê na lista (sem o rascunho, que tem rota própria)."""
    return {
        "id": intake.id,
        "label": decrypt_field(intake.label),
        "status": intake_status(intake),
        "created_at": _iso(_as_utc(intake.created_at)),
        "expires_at": _iso(_as_utc(intake.expires_at)),
        "started_at": _iso(_as_utc(intake.started_at)),
        "deadline_at": _iso(_as_utc(intake.deadline_at)),
        "submitted_at": _iso(_as_utc(intake.submitted_at)),
        "draft_saved_at": _iso(_as_utc(intake.draft_saved_at)),
        # RS2: incompleto mostra até quando fica guardado se ninguém agir.
        "discard_at": _iso(intake_discard_at(intake)),
        "has_draft": bool(intake.draft),
        "patient_id": intake.patient_id or "",
    }


def _seconds_left(intake: PatientIntake, now: datetime) -> int:
    return max(0, int((_as_utc(intake.deadline_at) - now).total_seconds()))


async def _intake_by_token(db: AsyncSession, token: str) -> tuple:
    """Link + dona. Desconhecido, cancelado ou de conta encerrada: a mesma resposta (410)."""
    intake = (await db.execute(select(PatientIntake).where(
        PatientIntake.token_hash == hash_token(token)))).scalar_one_or_none()
    owner = await db.get(User, intake.owner_id) if intake else None
    # Produto (administração): conta suspensa também desliga os links dela.
    if (intake is None or intake.revoked_at or owner is None or owner.closed_at is not None
            or owner.suspended_at is not None):
        raise HTTPException(status_code=410, detail=INTAKE_INVALID)
    return intake, owner


def _check_fill_secret(intake: PatientIntake, fill_secret: str) -> None:
    """O link começado só continua no navegador que começou (comparação em tempo constante)."""
    if not intake.fill_secret_hash or not hmac.compare_digest(intake.fill_secret_hash, hash_token(fill_secret)):
        raise HTTPException(status_code=403, detail=INTAKE_OTHER_DEVICE)


def _require_fill_time(intake: PatientIntake, now: datetime, grace: int = 0) -> None:
    """Dentro do tempo de preenchimento (com uma folga para a rede no último envio)."""
    if intake.submitted_at:
        raise HTTPException(status_code=410, detail=INTAKE_DONE)
    if not intake.started_at or now >= _as_utc(intake.deadline_at) + timedelta(seconds=grace):
        msg = ("O tempo para preencher acabou. O que você já preencheu foi enviado à sua psicóloga, "
               "que vai completar o cadastro com você.") if intake.draft else (
               "O tempo para preencher acabou. Peça um novo link à sua psicóloga.")
        raise HTTPException(status_code=410, detail={"code": "intake_expired", "msg": msg})


def _intake_draft(intake: PatientIntake) -> dict:
    return json.loads(decrypt_field(intake.draft)) if intake.draft else {}


async def _owned_intake(db: AsyncSession, intake_id: str, user: User) -> PatientIntake:
    intake = (await db.execute(select(PatientIntake).where(
        PatientIntake.id == intake_id, PatientIntake.owner_id == user.user_id))).scalar_one_or_none()
    if intake is None:
        raise HTTPException(status_code=404, detail="Link de cadastro não encontrado.")
    return intake


# --- Rotas da psicóloga ----------------------------------------------------
@api_router.post("/intake-links", dependencies=[Depends(rate_limit("intake-create", 30, 3600))])
async def create_intake_link(data: IntakeLinkInput, user: User = Depends(get_writer), db: AsyncSession = Depends(get_db)):
    """Gera o link (só com gravação liberada: cadastrar paciente novo é gravar).
    O link volta UMA vez, nesta resposta; o banco guarda só o hash."""
    await enforce(f"intake-create-user:{user.user_id}", 30, 3600)
    now = now_utc()
    pending = await db.scalar(select(func.count()).select_from(PatientIntake).where(
        PatientIntake.owner_id == user.user_id, PatientIntake.revoked_at.is_(None),
        PatientIntake.started_at.is_(None), PatientIntake.expires_at > now))
    if pending >= INTAKE_MAX_PENDING:
        raise HTTPException(status_code=409, detail={
            "code": "too_many_links",
            "msg": f"Você já tem {INTAKE_MAX_PENDING} links esperando o paciente começar. "
                   "Cancele algum ou espere vencerem (30 minutos)."})
    token = secrets.token_urlsafe(32)  # 256 bits
    intake = PatientIntake(owner_id=user.user_id, token_hash=hash_token(token), label=encrypt_field(data.label),
                           expires_at=now + timedelta(minutes=INTAKE_START_MINUTES))
    db.add(intake)
    await db.commit()
    await db.refresh(intake)
    await log_audit(db, user.user_id, user.email, "link_cadastro", "cadastro", intake.id,
                    f"Link de cadastro criado (vale {INTAKE_START_MINUTES} minutos para começar)")
    return {**_intake_public(intake), "url": frontend_link("/cadastro", token)}


@api_router.get("/intake-links")
async def list_intake_links(user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(PatientIntake).where(PatientIntake.owner_id == user.user_id)
                             .order_by(desc(PatientIntake.created_at)).limit(50))).scalars().all()
    return [_intake_public(row) for row in rows]


@api_router.post("/intake-links/{intake_id}/revoke")
async def revoke_intake_link(data: EndSessionsInput, intake_id: str = PathParam(max_length=40),
                             user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Cancela o link (enviado por engano, paciente errado). O que o paciente já
    tinha digitado é apagado junto: a tela pede confirmação antes."""
    intake = await _owned_intake(db, intake_id, user)
    if intake.submitted_at:
        raise HTTPException(status_code=409, detail={"code": "intake_done", "msg": "Este cadastro já foi concluído."})
    had_draft = bool(intake.draft)
    intake.revoked_at = now_utc()
    intake.draft = ""
    intake.fill_secret_hash = None
    await db.commit()
    await log_audit(db, user.user_id, user.email, "link_cadastro", "cadastro", intake.id,
                    "Link de cadastro cancelado" + (" (o que o paciente preencheu foi apagado)" if had_draft else ""))
    return _intake_public(intake)


@api_router.get("/intake-links/{intake_id}/draft")
async def intake_link_draft(intake_id: str = PathParam(max_length=40), user: User = Depends(get_active_user),
                            db: AsyncSession = Depends(get_db)):
    """O que o paciente preencheu antes de o tempo acabar (cadastro incompleto).
    Leitura de dado pessoal: fica na trilha de auditoria."""
    intake = await _owned_intake(db, intake_id, user)
    if intake_status(intake) != "incomplete":
        raise HTTPException(status_code=409, detail={"code": "not_incomplete", "msg": "Este link não tem cadastro incompleto."})
    await log_audit(db, user.user_id, user.email, "visualizar", "cadastro", intake.id,
                    "Cadastro incompleto aberto para revisão")
    return {"data": _intake_draft(intake)}


@api_router.post("/intake-links/{intake_id}/complete")
async def complete_intake(data: PatientInput, intake_id: str = PathParam(max_length=40),
                          user: User = Depends(get_writer), db: AsyncSession = Depends(get_db)):
    """A psicóloga completa o cadastro incompleto (com o paciente) e salva: vira
    paciente e o rascunho é apagado. Mesmas regras do cadastro manual."""
    intake = await _owned_intake(db, intake_id, user)
    if intake_status(intake) != "incomplete":
        raise HTTPException(status_code=409, detail={"code": "not_incomplete", "msg": "Este link não tem cadastro incompleto."})
    claimed = await db.execute(update(PatientIntake).where(
        PatientIntake.id == intake.id, PatientIntake.submitted_at.is_(None)).values(submitted_at=now_utc()))
    if claimed.rowcount != 1:  # completado ao mesmo tempo em outra aba
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "intake_done", "msg": "Este cadastro já foi concluído."})
    patient, consent_action = await _new_patient(db, user, data)
    intake.completed_by = "professional"
    intake.patient_id = patient.id
    intake.draft = ""
    await db.commit()
    await db.refresh(patient)
    await log_audit(db, user.user_id, user.email, "criar", "paciente", patient.id,
                    "Cadastro de paciente (completado a partir do link de cadastro)")
    if consent_action:
        await log_audit(db, user.user_id, user.email, consent_action, "paciente", patient.id,
                        "Termo de consentimento registrado")
    return patient_public(patient, user)


@api_router.post("/intake-links/{intake_id}/discard")
async def discard_intake(data: EndSessionsInput, intake_id: str = PathParam(max_length=40),
                         user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    """Descarta o cadastro incompleto (ex.: paciente desistiu). Pede confirmação na tela."""
    intake = await _owned_intake(db, intake_id, user)
    if intake_status(intake) != "incomplete":
        raise HTTPException(status_code=409, detail={"code": "not_incomplete", "msg": "Este link não tem cadastro incompleto."})
    intake.draft = ""
    intake.revoked_at = now_utc()
    await db.commit()
    await log_audit(db, user.user_id, user.email, "link_cadastro", "cadastro", intake.id, "Cadastro incompleto descartado")
    return _intake_public(intake)


# --- Rotas do paciente (públicas) ------------------------------------------
@api_router.post("/intake/open", dependencies=[Depends(rate_limit("intake-open", 60, 600))])
async def intake_open(data: IntakeTokenInput, db: AsyncSession = Depends(get_db)):
    """Página do paciente: quem recebe os dados e as regras. NÃO começa o tempo
    (a prévia automática de aplicativos de mensagem não consome o link)."""
    intake, owner = await _intake_by_token(db, data.token)
    status = intake_status(intake)
    if status in ("submitted", "completed"):
        raise HTTPException(status_code=410, detail=INTAKE_DONE)
    if status not in ("pending", "in_progress"):
        # Link que chegou a ser COMEÇADO: a mensagem explica o que houve com os dados
        # (quem começou tem o link e o segredo do aparelho). Nunca começado, cancelado
        # ou desconhecido: sempre a mesma resposta, sem revelar qual é o caso.
        if intake.started_at:
            _require_fill_time(intake, now_utc())
        raise HTTPException(status_code=410, detail=INTAKE_INVALID)
    return {
        "status": status,
        "professional_name": owner.name or "",
        "start_until": _iso(_as_utc(intake.expires_at)),
        "fill_minutes": INTAKE_FILL_MINUTES,
        "extension_minutes": INTAKE_EXTENSION_MINUTES,
        "max_extensions": INTAKE_MAX_EXTENSIONS,
        "consent_text": consent_term_text(owner),
    }


@api_router.post("/intake/start", dependencies=[Depends(rate_limit("intake-start", 20, 600))])
async def intake_start(data: IntakeTokenInput, db: AsyncSession = Depends(get_db)):
    """"Começar": 10 minutos e o link preso a este navegador. Só UM começo vale
    (atualização condicional no banco: dois cliques ou dois aparelhos ao mesmo
    tempo não abrem duas sessões)."""
    intake, owner = await _intake_by_token(db, data.token)
    now = now_utc()
    if intake.started_at:
        raise HTTPException(status_code=409, detail=INTAKE_OTHER_DEVICE)
    if intake_status(intake, now) != "pending":
        raise HTTPException(status_code=410, detail=INTAKE_INVALID)
    secret = secrets.token_urlsafe(32)
    claimed = await db.execute(update(PatientIntake).where(
        PatientIntake.id == intake.id, PatientIntake.started_at.is_(None)).values(
        started_at=now, deadline_at=now + timedelta(minutes=INTAKE_FILL_MINUTES), fill_secret_hash=hash_token(secret)))
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=409, detail=INTAKE_OTHER_DEVICE)
    await db.commit()
    await log_audit(db, owner.user_id, INTAKE_ACTOR, "link_cadastro", "cadastro", intake.id,
                    "O paciente começou a preencher o cadastro")
    return {"fill_secret": secret, "seconds_left": INTAKE_FILL_MINUTES * 60, "extensions_left": INTAKE_MAX_EXTENSIONS}


@api_router.post("/intake/resume", dependencies=[Depends(rate_limit("intake-resume", 60, 600))])
async def intake_resume(data: IntakeFillInput, db: AsyncSession = Depends(get_db)):
    """Recarregou a página ou a internet caiu: volta exatamente de onde parou."""
    intake, _owner = await _intake_by_token(db, data.token)
    _check_fill_secret(intake, data.fill_secret)
    now = now_utc()
    _require_fill_time(intake, now)
    return {"data": _intake_draft(intake), "seconds_left": _seconds_left(intake, now),
            "extensions_left": INTAKE_MAX_EXTENSIONS - (intake.extensions or 0)}


@api_router.put("/intake/draft", dependencies=[Depends(rate_limit("intake-draft", 600, 600))])
async def intake_save_draft(data: IntakeDraftInput, db: AsyncSession = Depends(get_db)):
    """Salva o que o paciente já digitou (criptografado). Sem auditoria a cada
    tecla: a trilha registra começo, prorrogação e envio."""
    intake, _owner = await _intake_by_token(db, data.token)
    _check_fill_secret(intake, data.fill_secret)
    now = now_utc()
    _require_fill_time(intake, now, grace=INTAKE_GRACE_SECONDS)
    await enforce(f"intake-draft:{intake.id}", 300, 600)
    payload = data.data.model_dump()
    filled = any(value not in ("", None) for value in payload.values())
    intake.draft = encrypt_field(json.dumps(payload, ensure_ascii=False)) if filled else ""
    intake.draft_saved_at = now
    await db.commit()
    return {"saved_at": _iso(now), "seconds_left": _seconds_left(intake, now)}


@api_router.post("/intake/extend", dependencies=[Depends(rate_limit("intake-extend", 30, 600))])
async def intake_extend(data: IntakeFillInput, db: AsyncSession = Depends(get_db)):
    """+10 minutos, só nos últimos 3 minutos e até 10 vezes (WCAG 2.2.1)."""
    intake, owner = await _intake_by_token(db, data.token)
    _check_fill_secret(intake, data.fill_secret)
    now = now_utc()
    _require_fill_time(intake, now)
    if (intake.extensions or 0) >= INTAKE_MAX_EXTENSIONS:
        raise HTTPException(status_code=409, detail={"code": "no_more_time",
                                                     "msg": "O tempo já foi prorrogado o máximo de vezes."})
    if _seconds_left(intake, now) > INTAKE_EXTEND_WINDOW_SECONDS:
        raise HTTPException(status_code=409, detail={"code": "too_early",
                                                     "msg": "Dá para pedir mais tempo nos últimos 3 minutos."})
    intake.deadline_at = _as_utc(intake.deadline_at) + timedelta(minutes=INTAKE_EXTENSION_MINUTES)
    intake.extensions = (intake.extensions or 0) + 1
    await db.commit()
    await log_audit(db, owner.user_id, INTAKE_ACTOR, "link_cadastro", "cadastro", intake.id,
                    f"O paciente pediu mais tempo (+{INTAKE_EXTENSION_MINUTES} minutos)")
    return {"seconds_left": _seconds_left(intake, now),
            "extensions_left": INTAKE_MAX_EXTENSIONS - intake.extensions}


@api_router.post("/intake/submit", dependencies=[Depends(rate_limit("intake-submit", 20, 600))])
async def intake_submit(data: IntakeSubmitInput, db: AsyncSession = Depends(get_db)):
    """Envia o cadastro: vira paciente da psicóloga, com o termo de consentimento
    registrado como evidência (cópia do texto, data e o IP na trilha).

    Idempotente: se a resposta se perdeu na rede e o paciente envia de novo,
    a resposta é "já recebido", sem criar outro paciente. Só UM envio reserva o
    link (atualização condicional), e a criação do paciente está na MESMA
    transação: se ela falhar, a reserva é desfeita e o paciente pode reenviar.
    """
    intake, owner = await _intake_by_token(db, data.token)
    _check_fill_secret(intake, data.fill_secret)
    if intake.submitted_at and intake.completed_by == "patient":
        return {"ok": True, "already": True}
    now = now_utc()
    _require_fill_time(intake, now, grace=INTAKE_GRACE_SECONDS)
    claimed = await db.execute(update(PatientIntake).where(
        PatientIntake.id == intake.id, PatientIntake.submitted_at.is_(None)).values(
        submitted_at=now, completed_by="patient"))
    if claimed.rowcount != 1:  # outro envio do mesmo paciente chegou antes
        await db.rollback()
        return {"ok": True, "already": True}
    patient, consent_action = await _new_patient(db, owner, data.data)
    intake.patient_id = patient.id
    intake.draft = ""
    await db.commit()
    await log_audit(db, owner.user_id, INTAKE_ACTOR, "criar", "paciente", patient.id,
                    "Cadastro enviado pelo paciente (link de cadastro)")
    if consent_action:
        await log_audit(db, owner.user_id, INTAKE_ACTOR, consent_action, "paciente", patient.id,
                        "Termo de consentimento registrado pelo paciente (link de cadastro)")
    return {"ok": True, "already": False}


# ---------------------------------------------------------------------------
# PRODUTO: ADMINISTRAÇÃO (tela /administracao no frontend)
# ---------------------------------------------------------------------------
# Quem administra o PsiGestão controla as contas das psicólogas: cortesia,
# prorrogar o teste grátis, encerrar as sessões e suspender/reativar.
# Proteções (todas aqui no servidor; a tela só mostra):
#   1. quem é administrador vem da configuração do SERVIDOR (ADMIN_EMAILS,
#      admin_access.py), nunca do banco nem de uma rota;
#   2. verificação em duas etapas OBRIGATÓRIA para usar a administração;
#   3. CADA ação pede de novo a senha (ou, para quem só entra pelo Google, o
#      código do aplicativo): um computador esquecido aberto não basta, e há
#      limite de tentativas;
#   4. NENHUM dado de paciente: a lista mostra a conta e só o NÚMERO de pacientes;
#   5. tudo na trilha de auditoria, nas DUAS contas: a afetada vê o que mudou
#      (como "administração do PsiGestão") e a do administrador guarda quem fez;
#      e a pessoa recebe um e-mail explicando a mudança;
#   6. quem não é administrador recebe 404: a área não se anuncia;
#   7. administrador não suspende nem desconecta a própria conta nem a de outro
#      administrador (evita trancar o sistema por engano ou por conta invadida).
# ---------------------------------------------------------------------------
ADMIN_ACTOR = "administração do PsiGestão"  # como a ação aparece na trilha da conta afetada
ADMIN_PAGE_SIZE = 25
ADMIN_TRIAL_DAYS = (7, 14, 30, 60, 90)
# A situação da assinatura é CALCULADA (billing.access_state), então o filtro por
# situação roda aqui, sobre as contas carregadas. Folgado para o tamanho do
# produto; acima disso a lista considera as contas mais recentes.
ADMIN_MAX_ACCOUNTS = 5000
ADMIN_STATUSES = ("all", "trialing", "active", "past_due", "read_only", "courtesy", "suspended")
ADMIN_VIEW_LOG_MINUTES = 15  # AD6: no máximo um registro de consulta por intervalo
ADMIN_NOT_FOUND = "Não encontrado."
ADMIN_MFA_REQUIRED = {
    "code": "admin_mfa_required",
    "msg": "Ative a verificação em duas etapas (Configurações > Minha conta) para usar a administração.",
}


def _clean_admin_text(value: str) -> str:
    """Motivo digitado pelo administrador: sem caractere nulo e numa linha só
    (vai para o e-mail e para a trilha)."""
    value = (value or "").strip()
    if "\x00" in value:
        raise ValueError("Texto com caractere inválido.")
    return " ".join(value.split())


class AdminConfirmInput(BaseModel):
    # Confirmação de CADA ação: a senha atual do administrador (ou, para quem
    # só entra pelo Google, o código de 6 dígitos do aplicativo autenticador).
    confirm: str = Field(min_length=1, max_length=128)


class AdminReasonInput(AdminConfirmInput):
    reason: str = Field(default="", max_length=200)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return _clean_admin_text(value)


class AdminCourtesyInput(AdminReasonInput):
    grant: bool


class AdminTrialInput(AdminReasonInput):
    days: int

    @field_validator("days")
    @classmethod
    def allowed_days(cls, value: int) -> int:
        if value not in ADMIN_TRIAL_DAYS:
            raise ValueError("Escolha 7, 14, 30, 60 ou 90 dias.")
        return value


class AdminSuspensionInput(AdminReasonInput):
    suspend: bool

    @model_validator(mode="after")
    def reason_when_suspending(self):
        if self.suspend and len(self.reason) < 5:
            raise ValueError("Escreva o motivo da suspensão (a pessoa recebe por e-mail).")
        return self


async def get_admin(user: User = Depends(get_active_user)) -> User:
    """Administrador (ADMIN_EMAILS) com a verificação em duas etapas ativa."""
    if not admin_access.is_admin(user):
        raise HTTPException(status_code=404, detail=ADMIN_NOT_FOUND)
    if user.mfa_enabled_at is None:
        raise HTTPException(status_code=403, detail=ADMIN_MFA_REQUIRED)
    return user


# Análise da administração (AD5): o mesmo código do aplicativo não vale duas
# vezes (_check_mfa_code). Quem só entra pelo Google e faz duas ações em menos
# de 30 s recebia "Código incorreto", sem saber que bastava esperar o próximo.
ADMIN_CODE_USED = {
    "code": "admin_code_used",
    "msg": "Este código já foi usado. Espere o próximo código do aplicativo (até 30 segundos) e tente de novo. "
           "Nada foi alterado.",
}
# AD3: confirmações erradas seguidas (em 15 min) que disparam o aviso por e-mail.
ADMIN_ALERT_FAILURES = 3


async def _admin_recipients(db: AsyncSession) -> list:
    """AD3: as contas que SÃO administradoras (lista do servidor + e-mail
    comprovado + conta ativa). Um e-mail digitado errado em ADMIN_EMAILS, sem
    conta, nunca recebe aviso nenhum (seria mandar dados a um estranho)."""
    emails = list(admin_access.admin_emails())
    if not emails:
        return []
    users = (await db.execute(select(User).where(func.lower(User.email).in_(emails)))).scalars().all()
    return [user for user in users if admin_access.is_admin(user)]


async def _confirm_admin(db: AsyncSession, admin: User, confirm: str) -> None:
    """Confere a senha (ou o código do aplicativo) antes de CADA ação.
    10 tentativas a cada 15 min por administrador; cada erro fica na trilha."""
    await enforce(f"admin-confirm:{admin.user_id}", 10, 900)
    if admin.password_hash:
        ok = await verify_password_async(confirm, admin.password_hash)
    else:
        code = confirm.strip()
        ok = await _check_mfa_code(db, admin, code) is not None
        # AD5: código certo, mas já usado numa ação anterior -> mensagem própria
        # (não é tentativa de adivinhar: não conta como erro nem vai para a trilha).
        if not ok and admin.mfa_last_step is not None and admin.mfa_secret:
            step = totp.verify(decrypt_field(admin.mfa_secret), code)
            if step is not None and step <= admin.mfa_last_step:
                raise HTTPException(status_code=403, detail=ADMIN_CODE_USED)
    if not ok:
        await log_audit(db, admin.user_id, admin.email, "admin_confirmacao_falha", "administracao", admin.user_id,
                        "Ação de administração recusada: confirmação incorreta")
        # AD3: na 3ª confirmação errada em 15 min, a dona da conta é avisada por
        # e-mail (alguém pode estar usando a sessão dela). Só na 3ª: um aviso por rajada.
        # Enviado AQUI (await), e não em BackgroundTasks: a rota termina em erro (403),
        # e o FastAPI não roda tarefas de fundo de uma resposta de erro.
        recent = await db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.owner_id == admin.user_id, AuditLog.action == "admin_confirmacao_falha",
            AuditLog.timestamp >= now_utc() - timedelta(minutes=15)))
        if recent == ADMIN_ALERT_FAILURES:
            await mailer.send_email(_security_notice(
                admin, "Confirmações erradas na administração",
                f"Houve {ADMIN_ALERT_FAILURES} tentativas seguidas de confirmar ações de administração com a "
                "senha ou o código errados, na sua conta."))
        raise HTTPException(status_code=403, detail={
            "code": "admin_confirm_invalid",
            "msg": ("Senha incorreta." if admin.password_hash else "Código incorreto.") + " Nada foi alterado."})


async def _admin_target(db: AsyncSession, user_id: str) -> User:
    target = await db.get(User, user_id)
    if target is None or target.closed_at is not None:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    return target


def _admin_protected(admin: User, target: User, own_msg: str) -> None:
    """Suspender e desconectar não valem para a própria conta nem para outro administrador."""
    if target.user_id == admin.user_id:
        raise HTTPException(status_code=409, detail={"code": "admin_self", "msg": own_msg})
    if admin_access.is_admin(target):
        raise HTTPException(status_code=409, detail={
            "code": "admin_protected",
            "msg": "Esta é uma conta de administração. Para mexer nela, tire o e-mail de ADMIN_EMAILS no servidor."})


async def _admin_stats(db: AsyncSession, user_ids: list) -> tuple:
    """Número de pacientes (sem os anonimizados) e o último acesso de cada conta.
    Só CONTAGENS e datas: nenhum dado de paciente sai daqui."""
    if not user_ids:
        return {}, {}
    counts = dict((await db.execute(
        select(Patient.owner_id, func.count()).where(Patient.owner_id.in_(user_ids), Patient.anonymized == False)  # noqa: E712
        .group_by(Patient.owner_id))).all())
    logins = dict((await db.execute(
        select(AuditLog.owner_id, func.max(AuditLog.timestamp)).where(
            AuditLog.owner_id.in_(user_ids), AuditLog.action == "entrar").group_by(AuditLog.owner_id))).all())
    activity = dict((await db.execute(
        select(UserSession.user_id, func.max(UserSession.last_activity_at)).where(
            UserSession.user_id.in_(user_ids)).group_by(UserSession.user_id))).all())
    last = {uid: max((d for d in (_as_utc(logins.get(uid)), _as_utc(activity.get(uid))) if d), default=None)
            for uid in user_ids}
    return counts, last


def _admin_account(user: User, admin: User, patients: int, last_access, now: Optional[datetime] = None) -> dict:
    """Uma linha da lista da administração (sem nada de paciente além do número)."""
    state = billing.access_state(user, now)
    return {
        "user_id": user.user_id,
        "name": user.name or "",
        "email": user.email,
        "created_at": _iso(_as_utc(user.created_at)),
        "last_access_at": _iso(last_access),
        "patients": int(patients or 0),
        "mfa_enabled": user.mfa_enabled_at is not None,
        "email_verified": user.email_verified_at is not None,
        "status": "suspended" if user.suspended_at else state["state"],
        "subscription": {key: state[key] for key in (
            "state", "plan", "trial_ends_at", "paid_until", "days_left", "grace_ends_at", "reason",
            "cancel_at_period_end")},
        "courtesy_reason": user.courtesy_reason or "",
        "has_paid": user.paid_until is not None,
        "suspended_at": _iso(_as_utc(user.suspended_at)),
        "suspended_reason": user.suspended_reason or "",
        "is_admin": admin_access.is_admin(user),
        "is_self": user.user_id == admin.user_id,
    }


async def _admin_done(db: AsyncSession, background: BackgroundTasks, admin: User, target: User, *, action: str,
                      entity: str, detail: str, subject: str, lines: list, button: Optional[tuple] = None) -> dict:
    """Depois de uma ação: trilha das duas contas, e-mail à pessoa e a linha atualizada."""
    await log_audit(db, target.user_id, ADMIN_ACTOR, action, entity, target.user_id, detail)
    await log_audit(db, admin.user_id, admin.email, action, "administracao", target.user_id, detail)
    background.add_task(mailer.send_email, mailer.admin_notice(
        target.email, target.name or "", subject, lines, legal.contact_info()["support_email"], button=button))
    # Análise da administração (AD3): TODAS as administradoras (inclusive quem
    # agiu) recebem um aviso de segurança. Antes, se uma conta de administração
    # fosse invadida, as suspensões e cortesias só apareciam na trilha. O aviso
    # identifica a conta afetada pelo ID (o nome e o e-mail ficam na tela).
    what = (f"A conta de administração {admin.email} fez uma ação no PsiGestão: {detail}. "
            f"Conta afetada: {target.user_id}.")
    for recipient in await _admin_recipients(db):
        background.add_task(mailer.send_email, _security_notice(recipient, "Ação feita na administração", what))
    counts, last = await _admin_stats(db, [target.user_id])
    return _admin_account(target, admin, counts.get(target.user_id, 0), last.get(target.user_id))


async def admin_config_report(db: AsyncSession) -> list:
    """Análise da administração (AD4): para cada e-mail de ADMIN_EMAILS, se a
    conta existe e cumpre os requisitos (e-mail comprovado, duas etapas, ativa).
    Usado na subida da API (só contagens no log) e por
    `python manage_billing.py admin verificar` (no console do servidor)."""
    emails = sorted(admin_access.admin_emails())
    if not emails:
        return []
    users = {user.email.strip().lower(): user for user in (await db.execute(
        select(User).where(func.lower(User.email).in_(emails)))).scalars().all()}
    report = []
    for email in emails:
        user = users.get(email)
        report.append({
            "email": email,
            "account": user is not None,
            "email_verified": bool(user and user.email_verified_at),
            "mfa_enabled": bool(user and user.mfa_enabled_at),
            "active": bool(user and user.closed_at is None and user.suspended_at is None),
            "ready": bool(user) and admin_access.is_admin(user) and user.mfa_enabled_at is not None,
        })
    return report


async def _log_admin_status() -> None:
    """AD4: na subida, avisa se algum e-mail de ADMIN_EMAILS não está pronto
    (ex.: digitado diferente do cadastro). O log leva só CONTAGENS, nunca e-mails."""
    if not admin_access.admin_emails():
        return
    try:
        async with AsyncSessionLocal() as db:
            report = await admin_config_report(db)
    except Exception:  # banco fora do ar na subida: a API sobe mesmo assim
        logger.warning("Não foi possível conferir ADMIN_EMAILS na subida.", exc_info=True)
        return
    ready = sum(1 for item in report if item["ready"])
    if ready < len(report):
        logger.warning("ADMIN_EMAILS: %d e-mail(s) configurado(s), %d pronto(s) para a administração "
                       "(conta com e-mail comprovado e duas etapas). Confira com: "
                       "python manage_billing.py admin verificar", len(report), ready)
    else:
        logger.info("ADMIN_EMAILS: %d administrador(es) pronto(s).", ready)


def _br_day(value: datetime) -> str:
    return _as_utc(value).astimezone(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y")


class AdminListInput(BaseModel):
    q: str = Field(default="", max_length=120)
    status: str = Field(default="all", max_length=20)
    page: int = Field(default=1, ge=1, le=1000)


# POST (e não GET com ?q=): a busca leva nome ou e-mail de uma pessoa, e a URL
# completa fica nos logs do proxy/servidor. Mesmo motivo de /patients/search.
@api_router.post("/admin/users/search", dependencies=[Depends(rate_limit("admin-read", 60, 60))])
async def admin_list_users(data: AdminListInput, admin: User = Depends(get_admin),
                           db: AsyncSession = Depends(get_db)):
    """Contas (sem as encerradas), das mais novas para as mais antigas, com busca
    por nome ou e-mail e filtro por situação. `summary`: quantas há em cada situação."""
    q, state_filter, page = data.q, data.status, data.page
    if state_filter not in ADMIN_STATUSES:
        raise HTTPException(status_code=422, detail="Filtro de situação inválido.")
    stmt = select(User).where(User.closed_at.is_(None))
    term = " ".join(q.split()).lower()
    if term:
        # Curingas do LIKE digitados na busca valem como texto.
        pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(func.lower(User.email).like(pattern, escape="\\"),
                              func.lower(func.coalesce(User.name, "")).like(pattern, escape="\\")))
    users = (await db.execute(stmt.order_by(desc(User.created_at)).limit(ADMIN_MAX_ACCOUNTS))).scalars().all()
    now = now_utc()
    status_of = {u.user_id: ("suspended" if u.suspended_at else billing.access_state(u, now)["state"]) for u in users}
    summary = {key: 0 for key in ADMIN_STATUSES}
    summary["all"] = len(users)
    for value in status_of.values():
        summary[value] = summary.get(value, 0) + 1
    if state_filter != "all":
        users = [u for u in users if status_of[u.user_id] == state_filter]
    total = len(users)
    pages = max(1, -(-total // ADMIN_PAGE_SIZE))
    page = min(page, pages)
    page_users = users[(page - 1) * ADMIN_PAGE_SIZE: page * ADMIN_PAGE_SIZE]
    counts, last = await _admin_stats(db, [u.user_id for u in page_users])
    # LGPD (art. 37): consultar dados das contas é tratamento; fica na trilha do
    # administrador (sem o termo buscado, que pode ser um nome).
    # Análise da administração (AD6): uma vez a cada ADMIN_VIEW_LOG_MINUTES por tipo
    # (lista / busca). Antes cada clique em filtro ou página gravava um registro, e
    # as ações de verdade se perdiam no meio. A trilha continua dizendo que houve
    # consulta naquele intervalo.
    detail = "Lista de contas consultada" + (" (com busca)" if term else "")
    logged_recently = await db.scalar(select(exists().where(
        AuditLog.owner_id == admin.user_id, AuditLog.action == "visualizar",
        AuditLog.entity_type == "administracao", AuditLog.detail == detail,
        AuditLog.timestamp >= now - timedelta(minutes=ADMIN_VIEW_LOG_MINUTES))))
    if not logged_recently:
        await log_audit(db, admin.user_id, admin.email, "visualizar", "administracao", admin.user_id, detail)
    return {
        "items": [_admin_account(u, admin, counts.get(u.user_id, 0), last.get(u.user_id), now) for u in page_users],
        "total": total, "page": page, "pages": pages, "page_size": ADMIN_PAGE_SIZE, "summary": summary,
        "trial_days_options": list(ADMIN_TRIAL_DAYS),
    }


@api_router.post("/admin/users/{user_id}/courtesy", dependencies=[Depends(rate_limit("admin-action", 30, 600))])
async def admin_courtesy(data: AdminCourtesyInput, background: BackgroundTasks, user_id: str = PathParam(max_length=40),
                         admin: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    """Concede ou revoga a cortesia (acesso completo, sem cobrança, até ser revogada)."""
    await _confirm_admin(db, admin, data.confirm)
    target = await _admin_target(db, user_id)
    if data.grant == (target.courtesy_at is not None):
        raise HTTPException(status_code=409, detail={"code": "no_change", "msg": (
            "Esta conta já tem cortesia." if data.grant else "Esta conta não tem cortesia.")})
    reason = _apply_courtesy(target, data.grant, data.reason)
    await db.commit()
    await db.refresh(target)
    if data.grant:
        detail = "Cortesia concedida pela administração" + (f" ({reason})" if reason else "")
        lines = ["A sua conta no PsiGestão recebeu acesso de cortesia: todos os recursos liberados, sem cobrança."]
        subject = "Cortesia liberada"
    else:
        detail = "Cortesia revogada pela administração" + (f" ({data.reason})" if data.reason else "")
        lines = ["A cortesia da sua conta no PsiGestão foi encerrada. Agora valem as regras normais da assinatura: "
                 "sem um plano ativo, a conta fica em modo somente leitura (você continua vendo e exportando tudo)."]
        subject = "Cortesia encerrada"
    return await _admin_done(db, background, admin, target, action="cortesia", entity="assinatura", detail=detail,
                             subject=subject, lines=lines)


@api_router.post("/admin/users/{user_id}/trial", dependencies=[Depends(rate_limit("admin-action", 30, 600))])
async def admin_extend_trial(data: AdminTrialInput, background: BackgroundTasks, user_id: str = PathParam(max_length=40),
                             admin: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    """Mais dias de teste grátis, contados do fim atual (ou de hoje, se já acabou)."""
    await _confirm_admin(db, admin, data.confirm)
    target = await _admin_target(db, user_id)
    if target.courtesy_at is not None:
        raise HTTPException(status_code=409, detail={"code": "no_change",
                                                     "msg": "Esta conta tem cortesia: prorrogar o teste não muda nada."})
    if target.paid_until is not None:
        # billing.access_state: o teste só vale para quem nunca teve período pago.
        raise HTTPException(status_code=409, detail={"code": "had_subscription", "msg": (
            "Esta conta já teve assinatura paga, e o teste grátis não vale mais para ela. "
            "Se quiser liberar sem cobrança, use a cortesia.")})
    target.trial_ends_at = max(now_utc(), billing.trial_end(target)) + timedelta(days=data.days)
    await db.commit()
    await db.refresh(target)
    until = _br_day(target.trial_ends_at)
    detail = f"Teste grátis prorrogado pela administração por {data.days} dias (até {until})" + (f": {data.reason}" if data.reason else "")
    return await _admin_done(db, background, admin, target, action="prorrogar_teste", entity="assinatura",
                             detail=detail, subject="Teste grátis prorrogado",
                             lines=[f"O seu teste grátis no PsiGestão foi prorrogado por {data.days} dias: "
                                    f"agora vai até {until}. Aproveite!"])


@api_router.post("/admin/users/{user_id}/end-sessions", dependencies=[Depends(rate_limit("admin-action", 30, 600))])
async def admin_end_sessions(data: AdminReasonInput, background: BackgroundTasks, user_id: str = PathParam(max_length=40),
                             admin: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    """Desconecta a conta de todos os aparelhos (ex.: suspeita de senha vazada)."""
    await _confirm_admin(db, admin, data.confirm)
    target = await _admin_target(db, user_id)
    _admin_protected(admin, target, "Para a sua própria conta, use Configurações > Minha conta > "
                                    "Sair dos outros dispositivos.")
    ended = await end_login_sessions(db, target)
    detail = (f"Sessões encerradas pela administração ({ended} aparelho{'s' if ended != 1 else ''})"
              + (f": {data.reason}" if data.reason else ""))
    lines = ["A administração do PsiGestão desconectou a sua conta de todos os aparelhos. "
             "Para continuar, entre de novo com a sua senha."]
    if data.reason:
        lines.append(f"Motivo: {data.reason}")
    lines.append("Se você não reconhece algum acesso recente, troque a senha em Configurações > Minha conta.")
    return await _admin_done(db, background, admin, target, action="encerrar_sessoes", entity="usuario",
                             detail=detail, subject="Sua conta foi desconectada", lines=lines)


@api_router.post("/admin/users/{user_id}/suspension", dependencies=[Depends(rate_limit("admin-action", 30, 600))])
async def admin_suspension(data: AdminSuspensionInput, background: BackgroundTasks, user_id: str = PathParam(max_length=40),
                           admin: User = Depends(get_admin), db: AsyncSession = Depends(get_db)):
    """Suspende (motivo obrigatório) ou reativa a conta. Suspensa: sai de todos os
    aparelhos, não entra mais e os links de cadastro dela param. Os dados ficam
    guardados, sem alteração (guarda do prontuário, CFP)."""
    await _confirm_admin(db, admin, data.confirm)
    target = await _admin_target(db, user_id)
    _admin_protected(admin, target, "Você não pode suspender a própria conta.")
    if data.suspend == (target.suspended_at is not None):
        raise HTTPException(status_code=409, detail={"code": "no_change", "msg": (
            "Esta conta já está suspensa." if data.suspend else "Esta conta não está suspensa.")})
    button = None
    if data.suspend:
        target.suspended_at = now_utc()
        target.suspended_reason = data.reason
        # Revisão de segurança (RS1): a suspensão DESATIVA A SENHA. Antes ela só
        # encerrava as sessões, e quem soubesse a senha (caso "uso da conta por
        # outra pessoa", Termos item 8) entrava de novo no modo restrito e podia
        # exportar todos os pacientes ou encerrar a conta. Agora a senha nova só
        # nasce pelo link mandado ao e-mail da conta: volta quem controla o e-mail,
        # a titular. Quem entra pelo Google continua provando a identidade nele.
        had_password = bool(target.password_hash)
        target.password_hash = None
        await db.commit()
        ended = await end_login_sessions(db, target)
        if had_password:
            # _issue_email_token também invalida links de senha pedidos antes.
            token = await _issue_email_token(db, PASSWORD_RESET, target.email, user_id=target.user_id,
                                             lifetime=timedelta(minutes=RESET_LINK_MINUTES))
            button = ("Criar nova senha", frontend_link("/redefinir-senha", token))
        detail = (f"Conta suspensa pela administração ({ended} sessão(ões) encerrada(s)"
                  + ("; senha desativada, link de nova senha enviado ao e-mail" if had_password else "")
                  + f"): {data.reason}")
        subject = "Sua conta foi suspensa"
        lines = ["A sua conta no PsiGestão foi suspensa pela administração.",
                 f"Motivo: {data.reason}",
                 # AD2: o que ela ainda pode fazer (modo restrito), e que nada foi apagado.
                 "Enquanto estiver suspensa, você entra no sistema só para exportar os seus dados e os dos seus "
                 "pacientes (guarda do prontuário e pedidos dos pacientes), cuidar da segurança da conta ou "
                 "encerrá-la. Não é possível registrar, agendar nem usar links de cadastro de pacientes. "
                 "Os seus dados e prontuários continuam guardados, sem nenhuma alteração.",
                 "Para contestar a suspensão, fale com o suporte (página Ajuda do PsiGestão)."]
        if button:
            # RS1: como entrar de novo (a senha antiga não vale mais).
            lines.append(f"Por segurança, a sua senha foi desativada. Para entrar, crie uma nova pelo botão abaixo "
                         f"(vale por {RESET_LINK_MINUTES} minutos). Depois disso, use \"Esqueci minha senha\" "
                         "na tela de entrada.")
    else:
        target.suspended_at = None
        target.suspended_reason = ""
        await db.commit()
        detail = "Conta reativada pela administração" + (f": {data.reason}" if data.reason else "")
        subject = "Sua conta foi reativada"
        lines = ["A sua conta no PsiGestão foi reativada. Você já pode entrar de novo, e está tudo como antes."]
        if target.password_hash is None and target.google_sub is None:
            # RS1: a senha foi desativada na suspensão e ainda não foi criada outra.
            lines.append("Se você ainda não criou a senha nova, use \"Esqueci minha senha\" na tela de entrada.")
    await db.refresh(target)
    return await _admin_done(db, background, admin, target, action="suspender" if data.suspend else "reativar",
                             entity="usuario", detail=detail, subject=subject, lines=lines, button=button)


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

    # CORREÇÃO (varredura de UI, defeito 2): sessão CANCELADA não é consulta.
    # Antes "Consultas no mês" e "Sessões futuras" somavam também as canceladas
    # (o painel mostrava 4 consultas quando uma das quatro tinha sido cancelada).
    # coalesce: sessões antigas podem ter status vazio (NULL), que vale como
    # "agendada"; sem ele, o SQL descartaria essas sessões da contagem.
    # Fluxo clínico (C4): falta também não é consulta (nem sessão futura).
    not_cancelled = _occupies_slot

    # Como start_time agora é DateTime nativo, comparamos diretamente com month_start (removida a função iso())
    stmt_sessoes = select(func.count()).select_from(Session).where(
        Session.owner_id == uid, Session.start_time >= month_start, not_cancelled,
    )
    sessions_month = await db.scalar(stmt_sessoes)

    stmt_records = select(func.count()).select_from(Record).where(Record.owner_id == uid)
    total_records = await db.scalar(stmt_records)

    # --- LÓGICA DO BÔNUS APLICADA AQUI ---
    # Conta sessões agendadas que vão ocorrer de hoje em diante
    stmt_upcoming = select(func.count()).select_from(Session).where(
        Session.owner_id == uid, Session.start_time >= now_utc(), not_cancelled,  # defeito 2: sem canceladas
    )
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

    # Análise de UI (O1): passos da lista "Primeiros passos" que dependem dos
    # dados (os da conta vêm em /auth/me). Qualquer paciente conta, até
    # arquivado; qualquer sessão também, até cancelada: o passo é "aprender a fazer".
    has_patients = await db.scalar(select(exists().where(Patient.owner_id == uid)))
    # Revisão de segurança (RS2): cadastros incompletos esperando revisão (o tempo do
    # link acabou com dados digitados). Antes só apareciam na janela dos links.
    incomplete_intakes = await db.scalar(select(func.count()).select_from(PatientIntake).where(
        PatientIntake.owner_id == uid, PatientIntake.revoked_at.is_(None), PatientIntake.submitted_at.is_(None),
        PatientIntake.started_at.is_not(None), PatientIntake.deadline_at <= now_utc(),
        PatientIntake.draft.is_not(None), PatientIntake.draft != ""))
    has_sessions = await db.scalar(select(exists().where(Session.owner_id == uid)))

    return {
        "total_patients": total_patients,
        "sessions_month": sessions_month,
        "total_records": total_records,
        "upcoming_sessions": upcoming, # Variável populada com a query dinâmica
        "recent_patients": recent_patients,
        "records_feed": feed,
        "onboarding": {"has_patients": bool(has_patients), "has_sessions": bool(has_sessions)},
        "incomplete_intakes": incomplete_intakes or 0,  # RS2
    }
# ---------------------------------------------------------------------------
# Funções Auxiliares de Serialização (Agenda)
# ---------------------------------------------------------------------------
def session_public(s: Session, record_id: Optional[str] = None) -> dict:
    return {
        "id": s.id,
        "patient_id": s.patient_id or "",
        # Item 13: título, nome do paciente e notas da agenda são criptografados.
        "patient_name": decrypt_field(s.patient_name),
        "title": decrypt_field(s.title),
        # O Frontend espera "start" e "end", mas no banco chamamos de start_time e end_time.
        # Sempre com fuso (_as_utc): o SQLite devolve a data sem fuso, e a mesma
        # data enviada de volta (ex.: remarcar, marcar falta) era recusada.
        "start": _as_utc(s.start_time).isoformat() if s.start_time else "",
        "end": _as_utc(s.end_time).isoformat() if s.end_time else "",
        "status": s.status or "agendada",
        "notes": decrypt_field(s.notes),
        # PRODUTO (agenda -> evolução): a evolução registrada para esta sessão,
        # se houver. A agenda e o painel mostram "Evolução registrada" ou
        # oferecem "Registrar evolução".
        "record_id": record_id or "",
        # Fluxo clínico (P1): série a que a sessão pertence ("" = avulsa).
        "series_id": s.series_id or "",
        "recurrence": s.recurrence or "",
    }


async def _record_ids_for_sessions(db: AsyncSession, session_ids: list[str]) -> dict:
    """{id da sessão: id da evolução} numa consulta só (pelo índice ix_records_session_id)."""
    if not session_ids:
        return {}
    rows = (await db.execute(select(Record.session_id, Record.id).where(Record.session_id.in_(session_ids)))).all()
    return {session_id: record_id for session_id, record_id in rows}


async def _bump_patient_from_session(db: AsyncSession, user: User, s: Session) -> None:
    """Sessão REALIZADA (e já ocorrida) conta como "última consulta" do paciente."""
    if s.status != "realizada" or not s.patient_id or _as_utc(s.start_time) > now_utc():
        return
    patient = (await db.execute(select(Patient).where(
        Patient.id == s.patient_id, Patient.owner_id == user.user_id,
    ))).scalar_one_or_none()
    if patient is not None:
        _bump_last_consultation(patient, s.start_time)


async def _session_patient_name(db: AsyncSession, user: User, data: SessionInput) -> str:
    """Nome do paciente da sessão, vindo do CADASTRO (ver _apply_session_input).
    Separado para a série (P1) consultar o paciente uma vez, e não por sessão."""
    if not data.patient_id:
        return data.patient_name or ""
    patient = (await db.execute(select(Patient).where(
        Patient.id == data.patient_id, Patient.owner_id == user.user_id,
    ))).scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=422, detail="Paciente não encontrado para este profissional.")
    return decrypt_field(patient.full_name)


async def _apply_session_input(db: AsyncSession, user: User, s: Session, data: SessionInput) -> None:
    """Item 13: grava os campos livres da agenda criptografados (criar e editar).

    Produto (item 17 da auditoria): o paciente vinculado precisa ser DESTE
    profissional, e o nome gravado vem do cadastro (não do que o navegador
    mandou), para a agenda nunca mostrar um nome que não bate com o paciente.
    """
    patient_name = await _session_patient_name(db, user, data)
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


async def _lock_owner_schedule(db: AsyncSession, owner_id: str) -> None:
    """Trava a agenda DESTE profissional até o fim da transação atual.

    SEGURANÇA (varredura, item 9): a checagem de conflito (abaixo) consultava o
    banco e só depois gravava. Dois agendamentos enviados ao mesmo tempo para
    o mesmo horário (duas abas, dois aparelhos, um duplo clique) passavam os
    dois pela checagem antes de qualquer um gravar, e o horário ficava duplicado.

    Por que não uma restrição de exclusão no Postgres: ela recusaria TODA
    sobreposição, e a agenda permite, de propósito, "salvar mesmo assim" um
    encaixe confirmado pelo profissional (allow_conflict). A trava resolve a
    corrida sem tirar essa escolha: criar/remarcar sessões do MESMO
    profissional passa a acontecer uma de cada vez (checar -> gravar), e
    profissionais diferentes não esperam uns pelos outros.

    pg_advisory_xact_lock é liberada sozinha no commit/rollback. No SQLite
    (testes e fallback de desenvolvimento) não existe; lá a função não faz nada.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    # A trava é identificada por um número de 64 bits derivado do profissional.
    key = int.from_bytes(hashlib.sha256(f"agenda:{owner_id}".encode()).digest()[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


# Fluxo clínico: sessões que OCUPAM o horário. Sem atendimento (cancelada,
# faltou) não ocupam. coalesce: sessões antigas com status vazio valem como
# "agendada" (antes `status != "cancelada"` descartava essas no SQL, e elas
# nunca geravam conflito).
_occupies_slot = func.coalesce(Session.status, "agendada").not_in(NO_SHOW_STATUSES)


async def _find_conflicts(db: AsyncSession, user: User, start: datetime, end: datetime,
                          exclude_ids: frozenset = frozenset()) -> list:
    """Sessões deste profissional que se sobrepõem a [start, end).

    A busca usa o índice (owner_id, start_time) e uma janela limitada pela
    duração máxima de uma sessão; a sobreposição exata é calculada aqui,
    igual no SQLite e no Postgres.
    """
    stmt = select(Session).where(
        Session.owner_id == user.user_id,
        _occupies_slot,
        Session.start_time < end,
        Session.start_time >= start - MAX_SESSION_DURATION,
    )
    if exclude_ids:
        stmt = stmt.where(Session.id.not_in(exclude_ids))
    candidates = (await db.execute(stmt)).scalars().all()
    return [c for c in candidates if _session_end(c.start_time, c.end_time) > _as_utc(start)]


def _conflict_error(conflicts: list) -> HTTPException:
    unique = list({c.id: c for c in conflicts}.values())  # a mesma sessão pode bater com várias datas da série
    unique.sort(key=lambda c: c.start_time)
    return HTTPException(status_code=409, detail={
        "code": "schedule_conflict",
        "msg": "Já existe sessão neste horário." if len(unique) == 1 else "Já existem sessões nestes horários.",
        "conflicts": [session_public(c) for c in unique],
    })


async def _check_conflicts(db: AsyncSession, user: User, data: SessionInput, exclude_id: Optional[str] = None) -> None:
    """PRODUTO: detecção de conflito de horário (antes era possível marcar
    dois pacientes no mesmo horário sem nenhum aviso).

    Sessões sem atendimento (canceladas e, desde o C4, faltas) não contam.
    """
    if data.status in NO_SHOW_STATUSES or data.allow_conflict:
        return
    conflicts = await _find_conflicts(db, user, data.start, _session_end(data.start, data.end),
                                      frozenset({exclude_id}) if exclude_id else frozenset())
    if conflicts:
        raise _conflict_error(conflicts)


def _series_dates(data: SessionInput) -> list:
    """P1: (início, término) de cada sessão a criar. Sem repetição, só a informada.
    O término acompanha a duração da primeira (ou fica vazio, como ela)."""
    count = data.repeat.count if data.repeat else 1
    step = timedelta(days=RECURRENCE_DAYS[data.repeat.frequency]) if data.repeat else timedelta(0)
    duration = (data.end - data.start) if data.end else None
    return [(data.start + step * i, (data.start + step * i + duration) if duration else None) for i in range(count)]

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

    records = await _record_ids_for_sessions(db, [s.id for s in docs])  # agenda -> evolução
    return [session_public(s, records.get(s.id)) for s in docs]

@api_router.post("/sessions")
async def create_session(data: SessionInput, user: User = Depends(get_writer),  # assinatura: grava
                         db: AsyncSession = Depends(get_db)):
    # Varredura, item 9: trava ANTES de checar; liberada no commit abaixo
    # (não pode haver commit entre a checagem e a gravação).
    await _lock_owner_schedule(db, user.user_id)

    # Fluxo clínico (P1): com `repeat`, cria a série inteira ("toda semana",
    # 12 sessões...). Antes cada sessão semanal era marcada à mão. O conflito é
    # conferido em TODAS as datas antes de gravar qualquer uma: ou a série
    # inteira entra, ou nada entra (com "salvar mesmo assim", entra mesmo com conflito).
    dates = _series_dates(data)
    if data.status not in NO_SHOW_STATUSES and not data.allow_conflict:
        conflicts = []
        for start, end in dates:
            conflicts += await _find_conflicts(db, user, start, _session_end(start, end))
        if conflicts:
            raise _conflict_error(conflicts)

    series_id = f"ser_{uuid.uuid4().hex[:12]}" if data.repeat else None
    patient_name = await _session_patient_name(db, user, data)
    created = []
    for start, end in dates:
        nova_sessao = Session(owner_id=user.user_id, series_id=series_id,
                              recurrence=data.repeat.frequency if data.repeat else None)
        nova_sessao.patient_id = data.patient_id or None
        nova_sessao.patient_name = encrypt_field(patient_name)
        nova_sessao.title = encrypt_field(data.title)
        nova_sessao.start_time, nova_sessao.end_time = start, end
        nova_sessao.status = data.status
        nova_sessao.notes = encrypt_field(data.notes)
        db.add(nova_sessao)
        created.append(nova_sessao)
    await _bump_patient_from_session(db, user, created[0])  # produto: "última consulta"
    await db.commit()
    await db.refresh(created[0])

    # Item 13: antes o detalhe era o título digitado (costuma conter o nome do paciente).
    detail = "Sessão agendada"
    if data.repeat:
        detail = (f"Série {RECURRENCE_LABELS[data.repeat.frequency]} agendada: "
                  f"{count_label(len(created), 'sessão', 'sessões')}")
    await log_audit(db, user.user_id, user.email, "criar", "agenda", created[0].id, detail)
    # Sessão nova ainda não tem evolução. `series_count`: quantas foram criadas (P1).
    return {**session_public(created[0]), "series_count": len(created)}

@api_router.put("/sessions/{sid}")
async def update_session(sid: str, data: SessionInput, user: User = Depends(get_writer),  # assinatura: grava
                         db: AsyncSession = Depends(get_db)):
    # Varredura, item 9: mesma trava da criação (remarcar também ocupa horário).
    await _lock_owner_schedule(db, user.user_id)
    stmt = select(Session).where(Session.id == sid, Session.owner_id == user.user_id)
    result = await db.execute(stmt)
    s = result.scalar_one_or_none()

    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    record_id = (await _record_ids_for_sessions(db, [s.id])).get(s.id)
    # Fluxo clínico (C4): sessão com evolução registrada ACONTECEU; marcar como
    # falta ou cancelada contradiria o prontuário.
    if record_id and data.status in NO_SHOW_STATUSES:
        raise HTTPException(status_code=409, detail=(
            "Esta sessão tem evolução registrada, então aconteceu: não pode ser marcada como falta ou cancelada."))

    # Fluxo clínico (P1): "esta e as próximas" de uma série. As próximas são as
    # sessões AGENDADAS da mesma série depois desta (as realizadas, faltas e
    # canceladas são histórico e ficam como estão). Elas recebem o mesmo
    # paciente, título, notas e duração, e andam o mesmo tanto no tempo (mudar
    # esta de terça 14h para quarta 15h leva as próximas para quarta 15h).
    following = []
    if data.apply_to == "following" and s.series_id:
        following = (await db.execute(select(Session).where(
            Session.owner_id == user.user_id, Session.series_id == s.series_id,
            Session.start_time > s.start_time, func.coalesce(Session.status, "agendada") == "agendada",
        ).order_by(Session.start_time))).scalars().all()
    shift = data.start - _as_utc(s.start_time)
    duration = (data.end - data.start) if data.end else None
    moved = [(f, _as_utc(f.start_time) + shift) for f in following]

    # A própria sessão (e as da série que vão mudar) não contam como conflito
    # (remarcar 10 min para frente, por exemplo).
    if data.status not in NO_SHOW_STATUSES and not data.allow_conflict:
        own = frozenset({sid, *(f.id for f in following)})
        conflicts = await _find_conflicts(db, user, data.start, _session_end(data.start, data.end), own)
        for _f, start in moved:
            conflicts += await _find_conflicts(db, user, start, _session_end(start, start + duration if duration else None), own)
        if conflicts:
            raise _conflict_error(conflicts)

    await _apply_session_input(db, user, s, data)
    for f, start in moved:
        f.patient_id, f.patient_name, f.title, f.notes = s.patient_id, s.patient_name, s.title, s.notes
        f.start_time, f.end_time = start, (start + duration if duration else None)
    await _bump_patient_from_session(db, user, s)  # produto: "última consulta"

    await db.commit()
    await db.refresh(s)

    # Item 14: alterações na agenda não eram auditadas.
    detail = f"Sessão alterada (status: {data.status})"
    if moved:
        detail += f"; {count_label(len(moved), 'próxima sessão da série alterada', 'próximas sessões da série alteradas')}"
    await log_audit(db, user.user_id, user.email, "editar", "agenda", sid, detail)
    return {**session_public(s, record_id), "updated_following": len(moved)}

@api_router.delete("/sessions/{sid}")
async def delete_session(sid: str,
                         # Fluxo clínico (P1): "this" (só esta) ou "following" (esta e as
                         # próximas AGENDADAS da série; ex.: o paciente encerrou o tratamento).
                         scope: Literal["this", "following"] = Query("this"),
                         user: User = Depends(get_writer),  # assinatura: grava
                         db: AsyncSession = Depends(get_db)):
    stmt = select(Session).where(Session.id == sid, Session.owner_id == user.user_id)
    result = await db.execute(stmt)
    s = result.scalar_one_or_none()

    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    following = []
    if scope == "following" and s.series_id:
        # Só as AGENDADAS e sem evolução: o que já aconteceu (realizada, falta,
        # cancelada) é histórico e fica.
        following = (await db.execute(select(Session).where(
            Session.owner_id == user.user_id, Session.series_id == s.series_id,
            Session.start_time > s.start_time, func.coalesce(Session.status, "agendada") == "agendada",
            ~select(Record.id).where(Record.session_id == Session.id).exists(),
        ))).scalars().all()
    for session in [s, *following]:
        await db.delete(session)
    await db.commit()

    # Item 14: exclusões na agenda também não eram auditadas.
    detail = "Sessão removida"
    if following:
        detail += f" e {count_label(len(following), 'próxima sessão da série', 'próximas sessões da série')}"
    await log_audit(db, user.user_id, user.email, "excluir", "agenda", sid, detail)
    return {"ok": True, "deleted": 1 + len(following)}


# ---------------------------------------------------------------------------
# Análise de UI (P2): agenda dentro da ficha do paciente
# ---------------------------------------------------------------------------
# A ficha não mostrava as próximas sessões nem permitia agendar: era preciso
# ir à agenda e procurar o paciente. Esta rota traz, numa chamada só:
#   - as próximas sessões (de agora em diante, inclusive canceladas, para o
#     profissional ver o que mudou), até `limit`;
#   - o total de próximas sessões que ocupam horário;
#   - o resumo do que já passou por status (realizadas, faltas, cancelamentos):
#     adesão ao tratamento à vista, sem abrir a agenda (fluxo clínico, C4).
# A leitura da ficha já fica na auditoria (GET /patients/{pid}).
PATIENT_SESSIONS_LIMIT = 5


@api_router.get("/patients/{pid}/sessions")
async def patient_sessions(pid: str, limit: int = Query(PATIENT_SESSIONS_LIMIT, ge=1, le=20),
                           user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    await _get_owned_patient(db, pid, user)  # 404 se não for deste profissional
    now = now_utc()
    mine = (Session.owner_id == user.user_id, Session.patient_id == pid)
    upcoming = (await db.execute(select(Session).where(*mine, Session.start_time >= now)
                                 .order_by(Session.start_time.asc()).limit(limit))).scalars().all()
    upcoming_total = await db.scalar(select(func.count()).select_from(Session).where(
        *mine, Session.start_time >= now, _occupies_slot))
    status = func.coalesce(Session.status, "agendada")
    past = dict((await db.execute(select(status, func.count()).where(*mine, Session.start_time < now)
                                  .group_by(status))).all())
    records = await _record_ids_for_sessions(db, [s.id for s in upcoming])
    return {
        "upcoming": [session_public(s, records.get(s.id)) for s in upcoming],
        "upcoming_total": upcoming_total,
        "past": {key: past.get(key, 0) for key in ("realizada", "faltou", "cancelada", "agendada")},
    }


# ---------------------------------------------------------------------------
# Fluxo clínico (C2): evoluções pendentes
# ---------------------------------------------------------------------------
# O painel mostrava só as sessões de HOJE; uma sessão de ontem sem evolução
# sumia de vista e era esquecida. O registro documental de cada atendimento é
# obrigação do psicólogo (Resolução CFP nº 001/2009). Pendente = sessão:
#   - com paciente (sem paciente não há prontuário);
#   - que já começou (antes de `before`, padrão: agora);
#   - agendada ou realizada (falta e cancelada não pedem evolução);
#   - sem evolução registrada;
#   - dos últimos PENDING_EVOLUTIONS_DAYS dias (pendências mais antigas que
#     isso viram histórico; o limite também mantém a consulta leve).
# Para resolver: registrar a evolução ou marcar a sessão como falta/cancelada.
PENDING_EVOLUTIONS_DAYS = 90


@api_router.get("/sessions/pending-evolutions")
async def pending_evolutions(
    before: Optional[datetime] = Query(None, description="só sessões que começaram antes disto (ISO com fuso)"),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_active_user),
    db: AsyncSession = Depends(get_db),
):
    now = now_utc()
    before = min(_as_utc(before), now) if before else now
    conditions = (
        Session.owner_id == user.user_id,
        Session.patient_id.is_not(None), Session.patient_id != "",
        Session.start_time >= now - timedelta(days=PENDING_EVOLUTIONS_DAYS),
        Session.start_time < before,
        func.coalesce(Session.status, "agendada").in_(("agendada", "realizada")),
        ~select(Record.id).where(Record.session_id == Session.id).exists(),
        # O paciente ainda existe (sessões guardam o id solto, sem chave estrangeira).
        select(Patient.id).where(Patient.id == Session.patient_id, Patient.owner_id == user.user_id).exists(),
    )
    total = await db.scalar(select(func.count()).select_from(Session).where(*conditions))
    rows = (await db.execute(select(Session).where(*conditions)
                             .order_by(desc(Session.start_time)).limit(limit))).scalars().all()
    return {"items": [session_public(r) for r in rows], "total": total, "window_days": PENDING_EVOLUTIONS_DAYS}

# ---------------------------------------------------------------------------
# SEGURANÇA MÉDIA: encerramento da conta do profissional (LGPD art. 18)
# ---------------------------------------------------------------------------
# A Política de Privacidade (legal.py, item 8) promete: "Depois do
# encerramento os dados são eliminados, exceto os que a lei obriga a guardar".
# Antes não existia como encerrar a conta. Agora (decisão de produto):
#   1. a pessoa pode baixar TUDO antes (GET /account/export: ZIP com o PDF de
#      cada paciente e um JSON completo, para cumprir a própria guarda);
#   2. ao encerrar (POST /account/close), na hora:
#      - ninguém mais entra na conta (sessões encerradas, sem senha, sem Google);
#      - pacientes SEM prontuário sob guarda são excluídos, com a agenda inteira;
#      - pacientes COM prontuário sob guarda (Resolução CFP nº 001/2009, 5 anos)
#        continuam criptografados, sem acesso de ninguém;
#      - o e-mail é liberado (pode criar uma conta nova); nome e CRP ficam, só
#        para identificar a autoria dos prontuários guardados;
#   3. a rotina de manutenção (purge_closed_accounts) exclui cada prontuário no
#      fim da guarda e, quando não sobra nada (e passaram os 6 meses de guarda
#      dos registros de acesso do Marco Civil), a trilha de auditoria e a conta.
ACCOUNT_CLOSE_CONFIRMATION = "ENCERRAR"
ACCESS_LOG_RETENTION = timedelta(days=183)  # Marco Civil da Internet, art. 15: 6 meses


def _safe_filename(text_value: str) -> str:
    """Nome de arquivo seguro dentro do ZIP (sem acentos, barras ou espaços)."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", text_value or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "_", plain).strip("_")[:60] or "paciente"


def _build_account_zip(user_name: str, user_email: str, entries: list, agenda: list) -> io.BytesIO:
    """Monta o ZIP da exportação completa (roda numa thread: o PDF é pesado)."""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        everything = []
        for pat, recs, consent_given_at, consent_terms, consent_revoked_at in entries:
            pdf = _render_patient_pdf(pat, recs, user_name, user_email,
                                      consent_given_at, consent_terms, consent_revoked_at)
            archive.writestr(f"pacientes/{_safe_filename(pat['full_name'])}_{pat['id']}.pdf", pdf.getvalue())
            everything.append({"paciente": pat, "prontuarios": recs})
        archive.writestr("psigestao_completo.json", json.dumps({
            "exportado_em": now_utc().isoformat(), "profissional": user_email,
            "pacientes": everything, "agenda": agenda,
        }, ensure_ascii=False, indent=2))
        archive.writestr("LEIA-ME.txt", (
            "Exportação completa do PsiGestão.\n\n"
            "- pacientes/: o prontuário de cada paciente em PDF;\n"
            "- psigestao_completo.json: todos os dados (pacientes, evoluções e agenda) em formato aberto.\n\n"
            "Estes arquivos contêm dados de saúde: guarde-os em local protegido (Resolução CFP nº 001/2009 "
            "e LGPD). A guarda dos prontuários é responsabilidade do(a) psicólogo(a).\n"
        ))
    buf.seek(0)
    return buf


@api_router.get("/account/export", dependencies=[Depends(rate_limit("account-export", 3, 3600))])
async def export_account(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Exportação COMPLETA da conta (portabilidade, LGPD art. 18, V).

    Usa get_current_user (e não get_active_user): quem não quer aceitar uma
    versão nova da política ainda tem direito de levar os próprios dados.
    """
    await enforce(f"account-export-user:{user.user_id}", 3, 3600)
    patients = (await db.execute(select(Patient).where(Patient.owner_id == user.user_id)
                                 .order_by(Patient.created_at))).scalars().all()
    entries = []
    for p in patients:
        records = (await db.execute(select(Record).where(Record.patient_id == p.id)
                                    .order_by(desc(Record.session_datetime)))).scalars().all()
        pat = patient_public(p, user)
        recs = await run_in_threadpool(lambda rows=records: [record_public(r) for r in rows])
        entries.append((pat, recs, p.consent_given_at, bool(p.consent_terms), p.consent_revoked_at))
    sessions = (await db.execute(select(Session).where(Session.owner_id == user.user_id)
                                 .order_by(Session.start_time))).scalars().all()
    agenda = [session_public(s) for s in sessions]

    buf = await run_in_threadpool(_build_account_zip, user.name or "", user.email, entries, agenda)
    await log_audit(db, user.user_id, user.email, "exportar", "usuario", user.user_id,
                    f"Exportação completa da conta ({count_label(len(entries), 'paciente', 'pacientes')})")
    filename = f"psigestao_exportacao_{now_utc():%Y%m%d}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def _close_account(db: AsyncSession, user: User) -> dict:
    """Aplica o encerramento (ver o comentário do bloco). Devolve o resumo."""
    now = now_utc()
    deleted, retained, last_deadline = 0, 0, None
    patients = (await db.execute(select(Patient).where(Patient.owner_id == user.user_id))).scalars().all()
    for p in patients:
        deadline = await retention_deadline(db, p.id)
        if deadline is None or deadline <= now:
            await db.delete(p)  # cascata: evoluções, versões e índice de busca
            deleted += 1
        else:
            p.archived_at = p.archived_at or now
            retained += 1
            last_deadline = deadline if last_deadline is None else max(last_deadline, deadline)
    # A agenda não é prontuário: sai inteira (as evoluções guardadas perdem
    # só o vínculo com a sessão, ON DELETE SET NULL).
    await db.execute(update(Record).where(Record.owner_id == user.user_id).values(session_id=None)
                     .execution_options(synchronize_session=False))
    await db.execute(delete(Session).where(Session.owner_id == user.user_id))
    await db.execute(delete(EmailToken).where(EmailToken.user_id == user.user_id))
    # Produto (cadastro pelo paciente): links e rascunhos de cadastro não são
    # prontuário: saem na hora.
    await db.execute(delete(PatientIntake).where(PatientIntake.owner_id == user.user_id))

    user.closed_at = now
    user.purge_after = max(last_deadline or now, now + ACCESS_LOG_RETENTION)
    # Libera o e-mail (a pessoa pode criar uma conta nova no futuro) e apaga o
    # que não é exigido por lei. Nome e CRP ficam: identificam o autor dos
    # prontuários sob guarda.
    user.email = f"encerrada+{user.user_id}@contas-encerradas.invalid"
    user.password_hash = None
    user.google_sub = None
    user.email_verified_at = None
    user.picture = ""
    user.consent_term_body = None
    user.draft_key = None
    user.mfa_secret = None
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    user.mfa_recovery_codes = None
    await db.commit()
    await end_login_sessions(db, user)
    return {"deleted_patients": deleted, "retained_patients": retained,
            "retained_until": _iso(last_deadline) if last_deadline else ""}


@api_router.post("/account/close", dependencies=[Depends(rate_limit("account-close", 5, 900))])
async def close_account(data: CloseAccountInput, background: BackgroundTasks, response: Response,
                        sess: UserSession = Depends(get_current_session), db: AsyncSession = Depends(get_db)):
    """Encerra a conta. Exige digitar ENCERRAR e confirmar a identidade (senha
    ou Google, e o código do aplicativo se a verificação em duas etapas
    estiver ativa): quem achar o computador logado não consegue encerrar."""
    user = sess.user
    await enforce(f"account-close-user:{user.user_id}", 5, 900)
    if data.confirmation.strip().upper() != ACCOUNT_CLOSE_CONFIRMATION:
        raise HTTPException(status_code=400, detail=f"Digite {ACCOUNT_CLOSE_CONFIRMATION} para confirmar.")
    if user.password_hash:
        if not data.password or not await verify_password_async(data.password, user.password_hash):
            raise HTTPException(status_code=403, detail={"code": "invalid_current_password",
                                                         "msg": "A senha atual está incorreta."})
    elif not data.credential or not google_identity_matches(await verify_google_credential(data.credential), user):
        raise HTTPException(status_code=403, detail="Confirme sua identidade com a conta Google desta conta.")
    if user.mfa_enabled_at is not None and await _check_mfa_code(db, user, data.code or "") is None:
        raise HTTPException(status_code=403, detail=MFA_INVALID_DETAIL)

    original_email, name = user.email, user.name or ""
    summary = await _close_account(db, user)
    until = summary["retained_until"]
    until_label = datetime.fromisoformat(until).strftime("%d/%m/%Y") if until else None
    await log_audit(db, user.user_id, original_email, "encerrar", "usuario", user.user_id,
                    f"Conta encerrada: {count_label(summary['deleted_patients'], 'paciente excluído', 'pacientes excluídos')}"
                    + (f"; {count_label(summary['retained_patients'], 'paciente', 'pacientes')} sob guarda até "
                       f"{until_label}" if summary["retained_patients"] else ""))
    background.add_task(mailer.send_email, mailer.account_closed(
        original_email, name, summary["deleted_patients"], summary["retained_patients"], until_label))
    clear_auth_cookie(response)
    return summary


async def _purge_audit_logs(db: AsyncSession, owner_id: str) -> None:
    """Apaga a trilha de uma conta encerrada (fim da guarda). A trilha é
    "somente inclusão" no banco; esta é a ÚNICA exceção (ver models.py)."""
    if db.get_bind().dialect.name == "postgresql":
        # SET LOCAL: vale só até o fim DESTA transação.
        await db.execute(text("SET LOCAL psigestao.audit_purge = 'on'"))
        await db.execute(delete(AuditLog).where(AuditLog.owner_id == owner_id))
    else:  # SQLite (testes e desenvolvimento sem Postgres): o gatilho sai e volta na mesma transação
        await db.execute(text("DROP TRIGGER IF EXISTS audit_logs_no_delete"))
        await db.execute(delete(AuditLog).where(AuditLog.owner_id == owner_id))
        await db.execute(text(
            "CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs "
            "BEGIN SELECT RAISE(ABORT, 'audit_logs e somente inclusao (append-only)'); END"))
    await db.commit()


async def purge_closed_accounts(db: AsyncSession, now: Optional[datetime] = None) -> dict:
    """Rotina de manutenção: exclui o que sobrou das contas encerradas quando o
    prazo de guarda de cada coisa termina. Segura para rodar várias vezes."""
    now = now or now_utc()
    purged_patients, purged_accounts = 0, 0
    closed = (await db.execute(select(User).where(User.closed_at.is_not(None)))).scalars().all()
    for user in closed:
        remaining = 0
        patients = (await db.execute(select(Patient).where(Patient.owner_id == user.user_id))).scalars().all()
        for p in patients:
            deadline = await retention_deadline(db, p.id)
            if deadline is None or deadline <= now:
                await db.delete(p)
                purged_patients += 1
            else:
                remaining += 1
        await db.commit()
        if remaining == 0 and (_as_utc(user.purge_after) or now) <= now:
            await _purge_audit_logs(db, user.user_id)
            await db.delete(user)
            await db.commit()
            purged_accounts += 1
    if purged_patients or purged_accounts:
        logger.info("Expurgo de contas encerradas: %d pacientes e %d contas excluídos.",
                    purged_patients, purged_accounts)
    return {"purged_patients": purged_patients, "purged_accounts": purged_accounts}


# ---------------------------------------------------------------------------
# Revisão de segurança (RS2): prazo do cadastro INCOMPLETO (LGPD art. 6º, III,
# e art. 15: dado pessoal só pelo tempo necessário).
#
# Antes, o que o paciente digitou antes de o tempo acabar ficava guardado para
# sempre se a psicóloga nunca revisasse, e ela só via esses cadastros abrindo a
# janela "Link de cadastro". Agora (rotina de manutenção, a cada poucas horas):
#   - 7 dias depois que o tempo acabou: um lembrete por e-mail (uma vez);
#   - 53 dias: aviso final ("será descartado em 7 dias");
#   - 60 dias, e pelo menos 7 dias depois do aviso final: o que o paciente
#     digitou é apagado (o link fica como cancelado, com registro na trilha).
# O descarte NUNCA acontece sem o aviso final antes. Conta suspensa ou encerrada
# fica com a contagem pausada (não consegue completar). E-mails só com números e
# datas: nada do paciente nem a identificação do link (pode ter o nome dele).
# O Painel mostra quantos incompletos existem (dashboard_stats).
# ---------------------------------------------------------------------------
INTAKE_INCOMPLETE_KEEP_DAYS = 60
INTAKE_REMIND_AFTER_DAYS = 7
INTAKE_DISCARD_NOTICE_DAYS = 7
INTAKE_SYSTEM_ACTOR = "sistema (manutenção)"


def intake_discard_at(intake: PatientIntake, now: Optional[datetime] = None) -> Optional[datetime]:
    """Quando o incompleto será descartado se ninguém agir (None se não for incompleto)."""
    now = now or now_utc()
    if intake_status(intake, now) != "incomplete":
        return None
    by_age = _as_utc(intake.deadline_at) + timedelta(days=INTAKE_INCOMPLETE_KEEP_DAYS)
    # Sem o aviso final ainda (ex.: conta suspensa), o descarte espera o aviso + 7 dias.
    warned = _as_utc(intake.discard_warned_at) or now
    return max(by_age, warned + timedelta(days=INTAKE_DISCARD_NOTICE_DAYS))


async def tend_incomplete_intakes(db: AsyncSession, now: Optional[datetime] = None) -> dict:
    """Lembretes, avisos finais e descartes dos cadastros incompletos (RS2)."""
    now = now or now_utc()
    rows = (await db.execute(select(PatientIntake).where(
        PatientIntake.revoked_at.is_(None), PatientIntake.submitted_at.is_(None),
        PatientIntake.started_at.is_not(None),
        PatientIntake.deadline_at <= now - timedelta(days=INTAKE_REMIND_AFTER_DAYS),
    ))).scalars().all()
    owners = {}
    reminders, warnings, discarded = {}, {}, 0
    for intake in rows:
        if intake_status(intake, now) != "incomplete":
            continue
        owner = owners.get(intake.owner_id) or await db.get(User, intake.owner_id)
        owners[intake.owner_id] = owner
        if owner is None or owner.closed_at is not None or owner.suspended_at is not None:
            continue  # contagem pausada: a dona não consegue completar agora
        age = now - _as_utc(intake.deadline_at)
        warned = _as_utc(intake.discard_warned_at)
        if (warned and age >= timedelta(days=INTAKE_INCOMPLETE_KEEP_DAYS)
                and now >= warned + timedelta(days=INTAKE_DISCARD_NOTICE_DAYS)):
            intake.draft = ""
            intake.fill_secret_hash = None
            intake.revoked_at = now
            await db.commit()
            await log_audit(db, owner.user_id, INTAKE_SYSTEM_ACTOR, "link_cadastro", "cadastro", intake.id,
                            f"Cadastro incompleto descartado automaticamente "
                            f"({INTAKE_INCOMPLETE_KEEP_DAYS} dias sem revisão, com aviso por e-mail antes)")
            discarded += 1
        elif not warned and age >= timedelta(days=INTAKE_INCOMPLETE_KEEP_DAYS - INTAKE_DISCARD_NOTICE_DAYS):
            intake.discard_warned_at = now
            warnings.setdefault(owner.user_id, []).append(intake)
        elif not warned and intake.reminded_at is None:
            intake.reminded_at = now
            reminders.setdefault(owner.user_id, []).append(intake)
    await db.commit()

    for owner_id in set(reminders) | set(warnings):
        owner = owners[owner_id]
        warned_list = warnings.get(owner_id, [])
        discard_date = min((intake_discard_at(i, now) for i in warned_list), default=None)
        await mailer.send_email(mailer.intake_incomplete_notice(
            owner.email, owner.name or "", len(reminders.get(owner_id, [])), len(warned_list),
            _br_day(discard_date) if discard_date else "", INTAKE_INCOMPLETE_KEEP_DAYS,
            frontend_link("/pacientes?cadastros=1")))
        for intake in reminders.get(owner_id, []) + warned_list:
            await log_audit(db, owner_id, INTAKE_SYSTEM_ACTOR, "link_cadastro", "cadastro", intake.id,
                            "Aviso de descarte do cadastro incompleto enviado por e-mail" if intake in warned_list
                            else "Lembrete do cadastro incompleto enviado por e-mail")
    return {"intake_reminders": sum(map(len, reminders.values())),
            "intake_discard_warnings": sum(map(len, warnings.values())),
            "intakes_discarded": discarded}


async def purge_patient_intakes(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Produto (cadastro pelo paciente): apaga links encerrados há mais de
    INTAKE_KEEP_DAYS dias (enviados, completados, cancelados, vencidos sem nada
    digitado). Cadastro INCOMPLETO com dados fica até a psicóloga completar ou
    descartar, ou até o prazo de 60 dias (tend_incomplete_intakes, RS2)."""
    now = now or now_utc()
    rows = (await db.execute(select(PatientIntake).where(
        PatientIntake.created_at < now - timedelta(days=INTAKE_KEEP_DAYS)))).scalars().all()
    purged = 0
    for intake in rows:
        if intake_status(intake, now) == "incomplete":
            continue
        await db.delete(intake)
        purged += 1
    await db.commit()
    return purged


# Rotina de manutenção: roda dentro da API (a cada MAINTENANCE_INTERVAL_HOURS)
# e também pode ser chamada por um agendador externo (python maintenance.py).
# No Postgres, uma trava consultiva garante que só UM worker rode por vez.
MAINTENANCE_LOCK_KEY = 73104201
MAINTENANCE_INTERVAL_HOURS = float(os.environ.get("MAINTENANCE_INTERVAL_HOURS", "6"))
MAINTENANCE_ENABLED = os.environ.get("MAINTENANCE_ENABLED", "true").strip().lower() != "false"


async def run_maintenance() -> dict:
    async with AsyncSessionLocal() as db:
        postgres = db.get_bind().dialect.name == "postgresql"
        if postgres and not await db.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK_KEY}):
            return {"skipped": "outro processo está rodando a manutenção"}
        try:
            result = await purge_closed_accounts(db)
            # RS2: lembretes/avisos/descarte dos incompletos ANTES da limpeza dos links.
            result.update(await tend_incomplete_intakes(db))
            result["purged_intakes"] = await purge_patient_intakes(db)
            return result
        finally:
            if postgres:
                await db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MAINTENANCE_LOCK_KEY})
                await db.commit()


async def _maintenance_loop() -> None:
    await asyncio.sleep(60)  # deixa o servidor terminar de subir
    while True:
        try:
            await run_maintenance()
        except Exception:
            logger.exception("Falha na rotina de manutenção (expurgo de contas encerradas / avisos).")
        await asyncio.sleep(MAINTENANCE_INTERVAL_HOURS * 3600)


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