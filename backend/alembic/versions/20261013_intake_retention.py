"""Cadastro incompleto com prazo de 60 dias (revisão de segurança, RS2).

Revision ID: 20261013_intake_retention
Revises: 20261010_admin
Create Date: 2026-10-13

- patient_intakes.reminded_at: lembrete por e-mail (7 dias depois que o tempo
  do paciente acabou) já enviado;
- patient_intakes.discard_warned_at: aviso final ("será descartado em 7 dias")
  já enviado. O descarte automático (60 dias) só acontece 7 dias depois dele.

Idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261013_intake_retention"
down_revision = "20261010_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("patient_intakes")}
    for name in ("reminded_at", "discard_warned_at"):
        if name not in existing:
            op.add_column("patient_intakes", sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("patient_intakes", "discard_warned_at")
    op.drop_column("patient_intakes", "reminded_at")
