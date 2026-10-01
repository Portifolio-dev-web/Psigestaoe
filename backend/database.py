# database.py
import logging
import os
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from environment import PRODUCTION, get_environment

DEV_SQLITE_URL = "sqlite+aiosqlite:///./psigestao_dev.db"


def resolve_database_url() -> str:
    """URL do banco, vinda da variável de ambiente (o docker-compose a define).

    SEGURANÇA (varredura, item 5): sem DATABASE_URL o sistema caía calado num
    arquivo SQLite local, INCLUSIVE em produção. Os prontuários iriam para um
    arquivo dentro do container, sem backup, e seriam apagados no próximo
    deploy. Agora o SQLite só é aceito no desenvolvimento; em produção (o
    padrão, ver environment.py) a falta da variável impede o servidor de subir.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    if get_environment() == PRODUCTION:
        logging.critical("SECURITY ALERT: DATABASE_URL ausente em produção. Abortando inicialização.")
        sys.exit(1)
    return DEV_SQLITE_URL


DATABASE_URL = resolve_database_url()


def _pool_options(url: str) -> dict:
    """Pool de conexões (ESCALA: workers).

    Antes a engine usava o pool padrão (5 + 10 extras) sem nenhum ajuste.
    Com vários workers isso importa, porque CADA processo tem o próprio pool:

        conexões possíveis = workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW)

    Esse total precisa ficar ABAIXO do max_connections do Postgres (100 por
    padrão, e alguns planos gerenciados permitem bem menos), sobrando folga
    para migrações, backups e acesso administrativo. Ex.: 4 workers × (5 + 5)
    = 40 conexões. Para passar disso, use um PgBouncer na frente do banco.
    """
    if url.startswith("sqlite"):
        return {}  # SQLite (testes e fallback local) não usa pool de rede
    return {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "5")),
        # Quanto esperar por uma conexão livre antes de falhar. Falhar rápido
        # (503 na fila) é melhor que empilhar requisições até todas expirarem.
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "10")),
        # Recicla conexões antigas: proxies e bancos gerenciados derrubam
        # conexões ociosas, e o erro só apareceria na próxima consulta.
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "1800")),
    }


# Criação da Engine Assíncrona.
# O pool_pre_ping=True verifica se a conexão está viva antes de usá-la, evitando quedas silenciosas.
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True, **_pool_options(DATABASE_URL))

# Fábrica de sessões (SessionLocal) que injetaremos no FastAPI
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Classe Base para todos os nossos modelos
Base = declarative_base()

# Dependência do FastAPI para gerenciar o ciclo de vida da transação por requisição
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
