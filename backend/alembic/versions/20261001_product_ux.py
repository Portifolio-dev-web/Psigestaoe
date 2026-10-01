"""Produto: bloqueio por inatividade e rascunho automático de prontuário.

Revision ID: 20261001_product_ux
Revises: 20260930_scale_indexes
Create Date: 2026-10-01

- users.lock_minutes: minutos sem uso até bloquear a tela (padrão 10);
- users.draft_key: chave (criptografada) dos rascunhos guardados no navegador;
- user_sessions.last_activity_at / locked_at / unlock_failures: bloqueio
  controlado no servidor (ver get_current_user em server.py).

Sessões já abertas começam sem last_activity_at; o servidor conta a
inatividade a partir da criação delas. É idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261001_product_ux"
down_revision = "20260930_scale_indexes"
branch_labels = None
depends_on = None


def _add_missing(table: str, columns: list) -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_missing("users", [
        sa.Column("lock_minutes", sa.Integer(), nullable=True, server_default="10"),
        sa.Column("draft_key", sa.Text(), nullable=True),
    ])
    _add_missing("user_sessions", [
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlock_failures", sa.Integer(), nullable=True, server_default="0"),
    ])


def downgrade() -> None:
    for column in ("unlock_failures", "locked_at", "last_activity_at"):
        op.drop_column("user_sessions", column)
    for column in ("draft_key", "lock_minutes"):
        op.drop_column("users", column)
