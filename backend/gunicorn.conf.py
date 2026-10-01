# gunicorn.conf.py
# ---------------------------------------------------------------------------
# Servidor de PRODUÇÃO com vários processos (ESCALA: workers).
#
# Antes o container rodava `uvicorn --reload`: UM processo (um núcleo de CPU,
# um event loop) e ainda vigiando arquivos, que é coisa de desenvolvimento.
# Uma rota lenta ou um travamento derrubava todo o atendimento.
#
# Agora o gunicorn gerencia vários workers uvicorn (assíncronos):
#   - usa todos os núcleos da máquina;
#   - reinicia um worker que morrer ou travar (timeout);
#   - recicla workers depois de N requisições (contém vazamentos de memória);
#   - reinício gracioso: requisições em andamento terminam antes da troca.
#
# O desenvolvimento continua com `uvicorn --reload` (docker-compose.yml).
# ---------------------------------------------------------------------------
import multiprocessing
import os

from uvicorn.workers import UvicornWorker

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"


class PsiGestaoUvicornWorker(UvicornWorker):
    """Worker uvicorn sem o cabeçalho `server: uvicorn`.

    SEGURANÇA (varredura, item 12): o cabeçalho anunciava a tecnologia do
    servidor em toda resposta, o que ajuda quem procura uma falha conhecida
    para explorar. Ele é acrescentado pelo próprio uvicorn, depois do app,
    então só se desliga aqui (no desenvolvimento: --no-server-header no
    docker-compose.yml).
    """

    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, "server_header": False}


# Workers ASSÍNCRONOS: cada um atende centenas de conexões. A regra
# "2 × núcleos + 1" vale para workers síncronos; aqui ~1 por núcleo basta.
# Lembre do limite de conexões do banco: workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW).
workers = int(os.environ.get("WEB_CONCURRENCY", multiprocessing.cpu_count()))
worker_class = PsiGestaoUvicornWorker

# Um worker que não responde em 60 s é morto e substituído.
timeout = int(os.environ.get("WORKER_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5

# Recicla cada worker após ~2000 requisições (o jitter evita que todos
# reiniciem juntos).
max_requests = int(os.environ.get("MAX_REQUESTS", "2000"))
max_requests_jitter = 200

# IP real do usuário atrás do proxy (rate limit e auditoria dependem disso).
# Liste aqui SÓ o IP do seu proxy/load balancer; "*" permitiria a qualquer um
# forjar o IP pelo cabeçalho X-Forwarded-For.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

# Log de acesso do gunicorn desligado: ele gravaria a URL com query string.
# O acesso é registrado pelo middleware (observability.py), só com o modelo da rota.
accesslog = None
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()


def child_exit(server, worker):
    """Métricas com vários processos: avisa o Prometheus que este worker
    morreu, para os valores dele não ficarem "presos" na soma."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
