"""Segurança (varredura, Fase 2): vínculo seguro com o Google e sessões com hash.

Revision ID: 20261001_account_security
Revises: 20261001_product_ux
Create Date: 2026-10-01

- users.google_sub: identificador fixo da conta Google (claim "sub"), único;
- users.email_verified_at: quando a posse do e-mail foi comprovada. Contas
  criadas pelo Google recebem a data de criação, porque o Google já tinha
  comprovado o e-mail; contas com senha ficam sem data, porque o cadastro
  não confirma o e-mail (ver google_auth em server.py);
- user_sessions.session_token -> token_hash: o banco passa a guardar só o
  SHA-256 do ID da sessão.

Todas as sessões abertas são encerradas: elas guardavam o ID em texto puro,
e os tokens antigos também não têm as claims agora obrigatórias (iss, aud,
iat). Cada profissional entra de novo uma vez. É idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261001_account_security"
down_revision = "20261001_product_ux"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    user_columns = {c["name"] for c in inspector.get_columns("users")}
    if "google_sub" not in user_columns:
        op.add_column("users", sa.Column("google_sub", sa.String(), nullable=True))
    if "email_verified_at" not in user_columns:
        op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
        op.execute("UPDATE users SET email_verified_at = created_at WHERE auth_provider = 'google'")
    if "ix_users_google_sub" not in {i["name"] for i in inspector.get_indexes("users")}:
        op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)

    session_columns = {c["name"] for c in inspector.get_columns("user_sessions")}
    if "session_token" in session_columns:
        op.execute("DELETE FROM user_sessions")
        op.alter_column("user_sessions", "session_token", new_column_name="token_hash")


def downgrade() -> None:
    op.execute("DELETE FROM user_sessions")
    op.alter_column("user_sessions", "token_hash", new_column_name="session_token")
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "google_sub")
