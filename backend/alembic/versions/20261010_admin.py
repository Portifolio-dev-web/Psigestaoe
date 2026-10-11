"""Administração: suspensão de conta (tela de administração).

Revision ID: 20261010_admin
Revises: 20261009_patient_intake
Create Date: 2026-10-10

- users.suspended_at / users.suspended_reason: conta suspensa pela
  administração (fraude, abuso). Suspensa, ninguém entra nela e os links de
  cadastro do paciente param de funcionar; os dados continuam guardados, sem
  alteração. NULL = conta normal.

Idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261010_admin"
down_revision = "20261009_patient_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "suspended_at" not in existing:
        op.add_column("users", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    if "suspended_reason" not in existing:
        op.add_column("users", sa.Column("suspended_reason", sa.String(), nullable=True, server_default=""))


def downgrade() -> None:
    op.drop_column("users", "suspended_reason")
    op.drop_column("users", "suspended_at")
