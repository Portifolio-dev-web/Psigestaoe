# observability.py
# ---------------------------------------------------------------------------
# Monitoramento (ESCALA): logs estruturados, ID de requisição, métricas e Sentry.
#
# Antes não havia nada além de prints soltos: sem saber quais rotas estão
# lentas, quantos erros 5xx acontecem ou qual requisição gerou um erro, não
# dá para operar o sistema com vários workers e várias instâncias.
#
# REGRA DE OURO (dados de saúde): NADA que identifique paciente pode sair
# do sistema por estes canais. Por isso:
#   - logs e métricas usam o MODELO da rota ("/api/patients/{pid}"), nunca a
#     URL real nem a query string;
#   - o log de acesso do uvicorn é desligado (ele registra URL + query string);
#   - o Sentry é configurado para nunca enviar corpo, cookies, cabeçalhos de
#     autenticação nem variáveis locais (que podem conter prontuário aberto).
# ---------------------------------------------------------------------------
import json
import logging
import os
import re
import time
import uuid
from contextvars import ContextVar

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from starlette.datastructures import MutableHeaders

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
access_logger = logging.getLogger("psigestao.access")

# ID aceito de quem chama (proxy/load balancer). Qualquer outra coisa é
# descartada: o valor vai para os logs e não pode permitir injeção de linhas.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

# Rotas consultadas a cada poucos segundos pela infraestrutura: registradas em
# DEBUG para não afogar os logs.
_QUIET_ROUTES = {"/api/health/live", "/api/health/ready", "/api/metrics"}


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Uma linha JSON por evento: é o formato que agregadores (Loki, CloudWatch,
    Datadog, ELK) indexam sem configuração extra."""

    _EXTRA_FIELDS = ("method", "route", "status", "duration_ms")

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for field in self._EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """LOG_FORMAT=json em produção; texto legível no desenvolvimento."""
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if os.environ.get("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    # O log de acesso do uvicorn grava a URL com a query string, e é aí que
    # ficaria, por exemplo, um termo de busca. O middleware abaixo registra o
    # acesso usando só o modelo da rota.
    logging.getLogger("uvicorn.access").disabled = True


# ---------------------------------------------------------------------------
# Métricas (Prometheus)
# ---------------------------------------------------------------------------
# Rótulos com cardinalidade controlada: método, MODELO da rota e status.
# Nunca usar ids, e-mails ou a URL real como rótulo (explodiria o número de
# séries e vazaria dados).
HTTP_REQUESTS = Counter(
    "psigestao_http_requests_total", "Requisições HTTP atendidas.", ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "psigestao_http_request_duration_seconds", "Tempo de resposta das requisições HTTP.", ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def render_metrics() -> tuple:
    """Conteúdo do /api/metrics.

    Com vários workers, cada processo tem os próprios contadores. Com
    PROMETHEUS_MULTIPROC_DIR definido (o start.sh define), cada processo grava
    em arquivos nessa pasta e aqui somamos todos; sem isso, cada coleta do
    Prometheus veria só o worker que atendeu aquela requisição.
    """
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST


# ---------------------------------------------------------------------------
# Middleware de requisição
# ---------------------------------------------------------------------------
class RequestContextMiddleware:
    """ID de requisição + tempo de resposta + métrica + log de acesso.

    Escrito como middleware ASGI "puro" (e não com @app.middleware("http")):
    o BaseHTTPMiddleware do Starlette cria uma tarefa extra e copia o corpo da
    resposta a cada requisição, um custo que se paga em todas as chamadas.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        # Não é resetado no fim: cada requisição roda em uma tarefa própria, e
        # mantê-lo deixa o ID também nos logs de erro gerados pelas camadas de fora.
        request_id_var.set(request_id)

        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Devolvido ao cliente: o usuário ou o suporte informa o ID e
                # achamos a requisição exata nos logs e no Sentry.
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed = time.perf_counter() - started
            route = scope.get("route")
            route_path = getattr(route, "path", None) or "unmatched"
            method = scope.get("method", "-")

            HTTP_REQUESTS.labels(method, route_path, str(status_code)).inc()
            HTTP_LATENCY.labels(method, route_path).observe(elapsed)

            if route_path in _QUIET_ROUTES:
                level = logging.DEBUG
            elif status_code >= 500:
                level = logging.ERROR
            else:
                level = logging.INFO
            access_logger.log(
                level, "%s %s %s %.1fms", method, route_path, status_code, elapsed * 1000,
                extra={"method": method, "route": route_path, "status": status_code,
                       "duration_ms": round(elapsed * 1000, 1)},
            )


# ---------------------------------------------------------------------------
# Sentry (opcional)
# ---------------------------------------------------------------------------
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-webhook-token"}


def scrub_sentry_event(event: dict, hint=None) -> dict:
    """Última barreira antes de um evento sair para o Sentry.

    As opções do init já desligam corpo, cookies e variáveis locais; esta
    função remove o que ainda sobrar (query string, cabeçalhos sensíveis, IP).
    """
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                name: ("[removido]" if name.lower() in _SENSITIVE_HEADERS else value)
                for name, value in headers.items()
            }
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
        user.pop("email", None)
    return event


def init_sentry(environment: str) -> bool:
    """Liga o Sentry só se SENTRY_DSN estiver definido."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    import sentry_sdk  # importado só quando usado

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=os.environ.get("APP_RELEASE") or None,
        # Rastreamento de desempenho: comece com uma amostra pequena (ex.: 0.05).
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
        send_default_pii=False,          # não envia IP, cookies nem dados do usuário
        max_request_body_size="never",   # NUNCA envia o corpo (cadastros, prontuários)
        include_local_variables=False,   # a pilha pode ter prontuário descriptografado
        before_send=scrub_sentry_event,
        before_send_transaction=scrub_sentry_event,
    )
    return True
