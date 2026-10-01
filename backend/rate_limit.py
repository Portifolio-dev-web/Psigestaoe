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
# ESCALA (workers): dois armazenamentos para o contador.
#   - Memória (padrão, desenvolvimento): janela deslizante dentro de UM
#     processo. Com vários workers/instâncias, cada processo conta separado
#     e o limite real vira limite × número de processos.
#   - Redis (produção com mais de um worker): defina REDIS_URL. Todos os
#     processos e instâncias passam a dividir o mesmo contador.
#   Em produção com WEB_CONCURRENCY > 1 e sem REDIS_URL, o servidor registra
#   um alerta na inicialização (warn_if_not_shared).
#
# IP do cliente atrás de proxy:
#   Em produção o app fica atrás de um proxy (Nginx, load balancer). Sem
#   configuração, todo mundo aparece com o IP do proxy e compartilha o mesmo
#   limite. O gunicorn.conf.py repassa FORWARDED_ALLOW_IPS ao uvicorn para que
#   request.client.host seja o IP real do usuário.
# ---------------------------------------------------------------------------
import logging
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

logger = logging.getLogger("psigestao.rate_limit")

# Permite desligar em testes de carga locais. Em produção deve ficar ligado.
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() != "false"
REDIS_URL = os.environ.get("REDIS_URL", "").strip()


class SlidingWindowLimiter:
    """Contador em memória, por processo (desenvolvimento e um único worker)."""

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


class RedisFixedWindowLimiter:
    """Contador compartilhado no Redis (ESCALA: vários workers e instâncias).

    Janela fixa: uma chave por janela de tempo, INCR + EXPIRE numa transação.
    É mais simples e barata que a janela deslizante; o custo conhecido é
    permitir até 2× o limite na virada entre duas janelas, aceitável para
    conter força bruta (que precisa de milhares de tentativas).
    """

    def __init__(self, url: str = "", client=None) -> None:
        if client is None:
            import redis.asyncio as redis  # importado só quando o Redis é usado

            client = redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._client = client

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        bucket = int(time.time() // window_seconds)
        redis_key = f"psigestao:rl:{key}:{bucket}"
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, window_seconds * 2)
                count, _ = await pipe.execute()
        except Exception:
            # Redis fora do ar: deixamos passar ("fail open") e registramos.
            # Bloquear todos os logins por uma falha de infraestrutura derrubaria
            # o sistema inteiro; a força bruta ainda esbarra no custo do bcrypt.
            logger.exception("Rate limit indisponível (Redis); requisição liberada sem contagem.")
            return True
        return int(count) <= limit


limiter = SlidingWindowLimiter()  # contador em memória (também usado pelos testes)
_redis_limiter = RedisFixedWindowLimiter(REDIS_URL) if REDIS_URL else None


def warn_if_not_shared(environment: str) -> None:
    workers = int(os.environ.get("WEB_CONCURRENCY", "1") or "1")
    if environment == "production" and workers > 1 and _redis_limiter is None:
        logger.critical(
            "Rate limit em memória com %s workers: cada processo conta separado e o limite real "
            "fica %sx maior. Defina REDIS_URL para compartilhar o contador.", workers, workers,
        )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


async def enforce(key: str, limit: int, window_seconds: int) -> None:
    """Levanta HTTP 429 quando a chave passa do limite. Usada também direto
    dentro das rotas (ex.: limite por e-mail no login).

    ESCALA: virou `async` para o caminho do Redis não bloquear o event loop.
    """
    if not RATE_LIMIT_ENABLED:
        return
    if _redis_limiter is not None:
        allowed = await _redis_limiter.hit(key, limit, window_seconds)
    else:
        allowed = limiter.hit(key, limit, window_seconds)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas. Aguarde alguns minutos e tente novamente.",
            headers={"Retry-After": str(window_seconds)},
        )


def rate_limit(scope: str, limit: int, window_seconds: int):
    """Dependência do FastAPI que limita por IP: Depends(rate_limit("login", 10, 60))."""

    async def dependency(request: Request) -> None:
        await enforce(f"{scope}:{client_ip(request)}", limit, window_seconds)

    return dependency
