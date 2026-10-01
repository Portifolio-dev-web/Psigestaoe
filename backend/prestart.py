# prestart.py
# ---------------------------------------------------------------------------
# Prepara o banco ANTES de os workers subirem (ESCALA: workers).
#
# Por que existe:
#   Antes, cada processo rodava create_all() ao iniciar. Com 4 workers e 2
#   instâncias, seriam 8 processos alterando o esquema ao mesmo tempo, e as
#   migrações do Alembic não eram aplicadas automaticamente em lugar nenhum.
#
# O que faz (chamado pelo start.sh, uma vez por container):
#   1. espera o banco aceitar conexões (o container da API pode subir antes);
#   2. pega uma trava no Postgres (pg_advisory_lock): se várias instâncias
#      sobem juntas, só uma migra; as outras esperam e depois não têm o que fazer;
#   3. banco vazio  -> cria as tabelas e marca a versão atual do Alembic;
#      banco existente -> aplica as migrações pendentes (alembic upgrade head);
#   4. indexa para busca os pacientes que ainda não estão no índice.
# ---------------------------------------------------------------------------
import asyncio
import logging
import os
import time

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import models  # noqa: F401  (registra as tabelas no metadata)
import rebuild_search_index
from database import DATABASE_URL, Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("psigestao.prestart")

# Número fixo e arbitrário que identifica a trava de migração deste sistema.
MIGRATION_LOCK_ID = 73019750
DB_WAIT_SECONDS = int(os.environ.get("DB_WAIT_SECONDS", "60"))
ALEMBIC_INI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alembic.ini")


async def wait_for_database(engine) -> None:
    deadline = time.monotonic() + DB_WAIT_SECONDS
    while True:
        try:
            async with engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
            return
        except Exception as exc:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Banco indisponível após {DB_WAIT_SECONDS}s") from exc
            log.info("Aguardando o banco de dados (%s)...", type(exc).__name__)
            await asyncio.sleep(2)


def migrate(sync_conn) -> str:
    """Roda na MESMA conexão que segura a trava (ver alembic/env.py)."""
    cfg = Config(ALEMBIC_INI)
    cfg.attributes["connection"] = sync_conn
    if not sa.inspect(sync_conn).has_table("users"):
        # Banco novo: o histórico do Alembic começa de um esquema já existente,
        # então o esquema inicial vem dos modelos e marcamos a versão atual.
        Base.metadata.create_all(sync_conn)
        command.stamp(cfg, "head")
        return "banco criado e marcado na versão atual"
    command.upgrade(cfg, "head")
    return "migrações aplicadas (alembic upgrade head)"


async def main() -> None:
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    await wait_for_database(engine)
    is_postgres = engine.dialect.name == "postgresql"

    async with engine.connect() as conn:
        if is_postgres:
            log.info("Aguardando a trava de migração...")
            await conn.execute(sa.text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})
            await conn.commit()
        try:
            result = await conn.run_sync(migrate)
            await conn.commit()
            log.info(result)
        finally:
            if is_postgres:
                await conn.execute(sa.text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})
                await conn.commit()
    await engine.dispose()

    indexed = await rebuild_search_index.rebuild(full=False)
    log.info("Índice de busca: %s paciente(s) indexado(s) agora.", indexed)


if __name__ == "__main__":
    asyncio.run(main())
