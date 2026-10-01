# http_security.py
# ---------------------------------------------------------------------------
# Proteções HTTP da API (SEGURANÇA, varredura itens 3 e 12):
#   1. limite de tamanho do corpo da requisição;
#   2. cabeçalhos de segurança em todas as respostas;
#   3. erro 422 sem repetir o que foi enviado;
#   4. conflito de gravação simultânea (IntegrityError) como 409 (item 9).
# Todos são registrados em server.py.
# ---------------------------------------------------------------------------
import json
import logging
import os

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import MutableHeaders

logger = logging.getLogger("psigestao.http")

# ---------------------------------------------------------------------------
# 1. Limite de tamanho do corpo
# ---------------------------------------------------------------------------
# Antes não havia limite nenhum. O FastAPI lê o corpo INTEIRO para a memória
# antes de validar e antes até do rate limit, inclusive em rotas sem login
# (/auth/login, /auth/google). Poucas requisições de centenas de MB derrubavam
# o servidor por falta de memória. 1 MB sobra para o maior uso legítimo (uma
# evolução de prontuário no limite de 50 mil caracteres ocupa ~200 KB).
MAX_REQUEST_BODY_BYTES = int(os.environ.get("MAX_REQUEST_BODY_BYTES", str(1024 * 1024)))


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Recusa com 413 qualquer corpo acima de MAX_REQUEST_BODY_BYTES.

    - Com Content-Length: decide antes de ler um único byte.
    - Sem Content-Length (envio em partes, "chunked"): lê até o limite e
      recusa ao passar dele; nunca guarda mais que o limite na memória.
    ASGI puro (sem BaseHTTPMiddleware), como os demais middlewares do projeto.
    """

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        too_large = f"Requisição grande demais (limite de {self.max_bytes // 1024} KB)."
        content_length = dict(scope["headers"]).get(b"content-length")
        if content_length is not None:
            if not content_length.isdigit() or int(content_length) > self.max_bytes:
                await _send_json(send, 413, too_large)
                return
            # O servidor HTTP (h11) garante que o corpo não passa do Content-Length.
            await self.app(scope, receive, send)
            return

        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # cliente desconectou
                await self.app(scope, receive, send)
                return
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                await _send_json(send, 413, too_large)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        buffered = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return buffered
            return await receive()  # depois do corpo: espera a desconexão normalmente

        await self.app(scope, replay, send)


# ---------------------------------------------------------------------------
# 2. Cabeçalhos de segurança
# ---------------------------------------------------------------------------
# Antes nenhuma resposta tinha cabeçalho de segurança. Estes valem para a API
# (JSON e PDF); os da interface (React) entram na Fase 7, com o servidor de
# arquivos estáticos.
SECURITY_HEADERS = {
    # O navegador não "adivinha" o tipo do arquivo: um JSON nunca vira HTML/script.
    "X-Content-Type-Options": "nosniff",
    # Nenhum site pode exibir a API dentro de um <iframe> (clickjacking).
    "X-Frame-Options": "DENY",
    # A API não carrega nada: qualquer HTML que ela devolvesse por engano não
    # executaria script, estilo, imagem ou formulário.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    # Endereços da API (com IDs de pacientes) não vazam para outros sites.
    "Referrer-Policy": "no-referrer",
    # Desliga recursos do navegador que a API nunca usa.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# Dados de saúde não podem ficar guardados em cache de navegador, proxy ou CDN
# (ex.: o próximo usuário de um computador compartilhado abrir o cache).
NO_STORE = "no-store"
# HSTS: o navegador passa a usar SÓ HTTPS com este domínio por 2 anos, mesmo
# que alguém digite http:// ou caia num link http:// (impede o rebaixamento
# para HTTP numa rede Wi-Fi hostil). Só em produção, que roda atrás de HTTPS.
HSTS = "max-age=63072000; includeSubDomains"
# A documentação automática (só existe no desenvolvimento) carrega scripts de
# uma CDN e quebraria com a CSP acima.
_DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    """Acrescenta os cabeçalhos acima a toda resposta, sem sobrescrever um
    valor que a rota já tenha definido."""

    def __init__(self, app, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        is_docs = path.startswith(_DOCS_PATHS)

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if is_docs and name == "Content-Security-Policy":
                        continue
                    if name not in headers:
                        headers[name] = value
                if path.startswith("/api/") and "Cache-Control" not in headers:
                    headers["Cache-Control"] = NO_STORE
                if self.hsts:
                    headers["Strict-Transport-Security"] = HSTS
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# 3. Erro 422 sem repetir o que foi enviado
# ---------------------------------------------------------------------------
# O padrão do FastAPI devolve, para cada campo inválido, o valor enviado
# (`input`) e detalhes internos (`ctx`). Uma senha fora da política, um CPF ou
# um trecho de prontuário voltavam inteiros no corpo da resposta e acabavam em
# logs de proxy, de monitoramento e do navegador. Agora a resposta diz QUAL
# campo e POR QUÊ, nunca O QUÊ.
#
# As mensagens padrão do pydantic vêm em inglês e iam direto para a tela
# ("Input should be a valid date..."). As mais comuns são traduzidas aqui.
_MESSAGES = {
    "missing": "Campo obrigatório.",
    "string_too_long": "Texto longo demais (máximo de {max_length} caracteres).",
    "date_parsing": "Data inválida.",
    "date_from_datetime_parsing": "Data inválida.",
    "datetime_parsing": "Data e hora inválidas.",
    "datetime_from_date_parsing": "Data e hora inválidas.",
    "literal_error": "Valor não permitido.",
    "json_invalid": "O corpo da requisição não é um JSON válido.",
}


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for error in exc.errors():
        message = str(error.get("msg", "Valor inválido."))
        template = _MESSAGES.get(error.get("type", ""))
        if template:
            # O `ctx` só preenche o limite na mensagem; ele nunca é devolvido.
            message = template.format(max_length=(error.get("ctx") or {}).get("max_length", "?"))
        # O pydantic prefixa as mensagens dos nossos validadores com
        # "Value error, ": o usuário vê só a mensagem em português.
        errors.append({
            "loc": list(error.get("loc", ())),
            "msg": message.removeprefix("Value error, "),
            "type": error.get("type", "value_error"),
        })
    return JSONResponse(status_code=422, content={"detail": errors})


# ---------------------------------------------------------------------------
# 4. Conflito de gravação simultânea (IntegrityError) -> 409, nunca 500
# ---------------------------------------------------------------------------
# SEGURANÇA (varredura, item 9): quando duas requisições simultâneas tentam
# gravar algo que o banco exige que seja único (e-mail, vínculo com o Google,
# token), a segunda recebia um erro 500. Pior: a mensagem do banco traz o
# valor repetido (ex.: "Key (email)=(fulano@...) already exists"), que ia
# parar no log. Rotas com tratamento próprio (como o cadastro) continuam com
# a mensagem delas; este é o tratamento padrão para todas as outras.
async def integrity_error_handler(_request: Request, exc: IntegrityError) -> JSONResponse:
    # Só o TIPO do erro vai para o log, nunca a mensagem (que contém os valores).
    logger.warning("Conflito de gravação simultânea no banco (%s).", type(exc.orig).__name__)
    return JSONResponse(status_code=409, content={"detail": {
        "code": "concurrent_write",
        "msg": "Esta informação foi alterada ao mesmo tempo em outra tela ou aparelho. Tente novamente.",
    }})
