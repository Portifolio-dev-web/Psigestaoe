# rate_limit.py
# ---------------------------------------------------------------------------
# CORREÇÃO DE SEGURANÇA (item 9 da auditoria): limite de requisições.
#
# Antes, as rotas de login, cadastro, login Google e webhook aceitavam
# tentativas ilimitadas. Isso permitia:
#   - força bruta de senha no /auth/login;
#   - criação em massa de contas no /auth/register;
#   - flood de pacientes falsos pelo webhook do Google Forms.
#
# Implementação: janela deslizante em memória, sem dependências novas.
#
# LIMITAÇÃO CONHECIDA (importante para escalar):
#   O contador vive na memória de UM processo. Com vários workers/instâncias,
#   cada um conta separadamente e o limite efetivo é multiplicado. Ao escalar
#   horizontalmente, troque por um backend compartilhado (Redis, ex.: a lib
#   `slowapi` com storage_uri="redis://...") ou aplique o limite no proxy
#   reverso / WAF (Nginx `limit_req`, Cloudflare).
#
# IP do cliente atrás de proxy:
#   Em produção o app fica atrás de um proxy (Nginx, load balancer). Sem
#   configuração, todo mundo aparece com o IP do proxy e compartilha o mesmo
#   limite. Rode o uvicorn com `--proxy-headers --forwarded-allow-ips=<IP do proxy>`
#   para que request.client.host seja o IP real do usuário.
# ---------------------------------------------------------------------------
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# Permite desligar em testes de carga locais. Em produção deve ficar ligado.
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "false"


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Registra uma tentativa. Retorna False se o limite da janela estourou."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            # Descarta as tentativas que já saíram da janela.
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


def enforce(key: str, limit: int, window_seconds: int) -> None:
    """Levanta HTTP 429 quando a chave passa do limite. Usada também direto
    dentro das rotas (ex.: limite por e-mail no login)."""
    if RATE_LIMIT_ENABLED and not limiter.hit(key, limit, window_seconds):
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas. Aguarde alguns minutos e tente novamente.",
            headers={"Retry-After": str(window_seconds)},
        )


def rate_limit(scope: str, limit: int, window_seconds: int):
    """Dependência do FastAPI que limita por IP: Depends(rate_limit("login", 10, 60))."""

    async def dependency(request: Request) -> None:
        enforce(f"{scope}:{client_ip(request)}", limit, window_seconds)

    return dependency
