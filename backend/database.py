# database.py
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

# A variável de ambiente que configuramos no docker-compose
DATABASE_URL = os.environ.get("DATABASE_URL") or "sqlite+aiosqlite:///./psigestao_dev.db"

# Criação da Engine Assíncrona. 
# O pool_pre_ping=True verifica se a conexão está viva antes de usá-la, evitando quedas silenciosas.
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

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