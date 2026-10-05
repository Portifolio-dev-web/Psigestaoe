"""Fluxo clínico: sessões recorrentes (P1) e responsável legal de menores (C3).

Revision ID: 20261005_clinical_flow
Revises: 20261003_account_lifecycle
Create Date: 2026-10-05

- sessions.series_id / sessions.recurrence: sessões criadas juntas como série
  ("toda semana", "a cada 2 semanas"), para alterar ou remover "esta e as
  próximas". Índice em series_id (consulta das próximas sessões da série).
- patients.guardian_*: responsável legal do paciente menor de 18 anos
  (nome, CPF e telefone gravados criptografados pela aplicação).
O status "faltou" (C4) não precisa de migração: a coluna status é texto.

Idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261005_clinical_flow"
down_revision = "20261003_account_lifecycle"
branch_labels = None
depends_on = None


def _columns(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _add_missing(table: str, columns: list) -> None:
    existing = _columns(table)
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_missing("sessions", [
        sa.Column("series_id", sa.String(), nullable=True),
        sa.Column("recurrence", sa.String(), nullable=True),
    ])
    if "ix_sessions_series_id" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("sessions")}:
        op.create_index("ix_sessions_series_id", "sessions", ["series_id"])
    _add_missing("patients", [
        sa.Column("guardian_name", sa.String(), nullable=True, server_default=""),
        sa.Column("guardian_cpf", sa.Text(), nullable=True, server_default=""),
        sa.Column("guardian_relationship", sa.String(), nullable=True, server_default=""),
        sa.Column("guardian_phone", sa.String(), nullable=True, server_default=""),
    ])


def downgrade() -> None:
    for column in ("guardian_phone", "guardian_relationship", "guardian_cpf", "guardian_name"):
        op.drop_column("patients", column)
    op.drop_index("ix_sessions_series_id", table_name="sessions")
    op.drop_column("sessions", "recurrence")
    op.drop_column("sessions", "series_id")
