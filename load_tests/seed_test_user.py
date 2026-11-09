# load_tests/seed_test_user.py
# ---------------------------------------------------------------------------
# Cria (ou reaproveita) UMA conta local só para o teste de carga do k6: pula
# o fluxo de registro + confirmação por e-mail, que não serve pra isto.
#
# SOMENTE LOCAL/STAGING. Marca a conta com courtesy_at (acesso de escrita
# garantido, sem depender da janela de trial) e aceite dos termos vigentes,
# para passar por get_active_user/get_writer sem precisar usar a UI.
#
# Uso (dentro do container da API, que já tem DATABASE_URL etc. no ambiente):
#   docker compose -f docker-compose.prod.yml cp load_tests/seed_test_user.py api:/app/seed_test_user.py
#   docker compose -f docker-compose.prod.yml exec \
#       -e SEED_EMAIL=loadtest@local.test -e SEED_PASSWORD="umaSenhaQualquer123" \
#       api python seed_test_user.py
# ---------------------------------------------------------------------------
import asyncio
import os
import sys

import bcrypt
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import AsyncSessionLocal  # noqa: E402
from legal import PRIVACY_POLICY_VERSION  # noqa: E402
from models import User  # noqa: E402
from environment import get_environment, PRODUCTION  # noqa: E402

from datetime import datetime, timezone

EMAIL = os.environ.get("SEED_EMAIL", "loadtest@local.test").strip().lower()
PASSWORD = os.environ.get("SEED_PASSWORD", "").strip()


async def main() -> None:
    if not PASSWORD:
        print("Defina SEED_PASSWORD (senha da conta de teste).", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    password_hash = bcrypt.hashpw(PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
        if user is None:
            user = User(email=EMAIL)
            db.add(user)
            print(f"Criando usuário de teste: {EMAIL}")
        else:
            print(f"Usuário de teste já existe, atualizando: {EMAIL}")

        user.password_hash = password_hash
        user.terms_accepted = True
        user.terms_version = PRIVACY_POLICY_VERSION
        user.terms_accepted_at = now
        user.courtesy_at = now  # garante permissão de escrita (get_writer) sem depender do trial
        user.suspended_at = None

        await db.commit()

    env = get_environment()
    if env == PRODUCTION:
        print("ATENÇÃO: ENVIRONMENT=production neste container. Use isto só em staging/local, nunca no banco real.")
    print(f"OK: {EMAIL} pronto para TEST_EMAIL/TEST_PASSWORD do k6.")


if __name__ == "__main__":
    asyncio.run(main())
