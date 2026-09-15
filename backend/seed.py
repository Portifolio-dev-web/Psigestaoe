# seed.py
import asyncio
import os
import uuid
import bcrypt
from sqlalchemy.future import select
from database import AsyncSessionLocal
from models import User

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

async def seed_admin():
    admin_email = os.environ.get("ADMIN_EMAIL", "").lower().strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "")

    if not admin_email or not admin_password:
        print("Aviso: ADMIN_EMAIL ou ADMIN_PASSWORD não configurados no .env")
        return

    # Usamos o context manager para garantir que a sessão feche
    async with AsyncSessionLocal() as db:
        stmt = select(User).where(User.email == admin_email)
        result = await db.execute(stmt)
        admin = result.scalar_one_or_none()

        if not admin:
            print(f"Fazendo o seed do Administrador: {admin_email}")
            novo_admin = User(
                user_id=f"user_{uuid.uuid4().hex[:12]}",
                email=admin_email,
                name="Administrador",
                password_hash=hash_password(admin_password),
                auth_provider="email",
                role="psicologo", # ou "admin", caso você expanda a role
                terms_accepted=True
            )
            db.add(novo_admin)
            await db.commit()
            print("✅ Administrador criado com sucesso!")
        else:
            print(f"O administrador {admin_email} já existe.")
            # Opcional: Atualiza a senha caso tenha mudado no .env
            admin.password_hash = hash_password(admin_password)
            await db.commit()
            print("✅ Senha sincronizada com o arquivo de ambiente.")

if __name__ == "__main__":
    print("Iniciando rotina de Seed...")
    asyncio.run(seed_admin())
    print("Rotina finalizada.")