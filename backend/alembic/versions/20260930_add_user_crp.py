"""Add professional CRP to users (item 6 da auditoria: termo de consentimento por profissional).

Revision ID: 20260930_user_crp
Revises: 20260917_consent
Create Date: 2026-09-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_user_crp"
down_revision = "20260917_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotente: o server.py ainda roda create_all() no startup, então num
    # banco novo a coluna pode já ter sido criada antes desta migração rodar.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "crp" not in columns:
        op.add_column("users", sa.Column("crp", sa.String(), nullable=True, server_default=""))


def downgrade() -> None:
    op.drop_column("users", "crp")
