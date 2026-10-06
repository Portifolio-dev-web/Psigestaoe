"""Produtividade e primeiro uso (análise de UI: P5, P7 e O1).

Revision ID: 20261006_productivity
Revises: 20261005_clinical_flow
Create Date: 2026-10-06

- users.agenda_start_hour / users.agenda_end_hour (P7): horário de atendimento
  mostrado na grade da agenda (NULL = padrão do servidor, 07:00 às 21:00).
- users.consent_term_reviewed_at / users.onboarding_dismissed_at (O1): passos
  da lista "Primeiros passos" do painel.
- patients.search_index_version (P5): versão do índice cego de busca. A versão
  2 indexa também telefone e CPF; os pacientes existentes ficam com NULL e são
  reindexados pelo rebuild_search_index.py (o prestart roda a cada implantação).

Idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261006_productivity"
down_revision = "20261005_clinical_flow"
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
    _add_missing("users", [
        sa.Column("agenda_start_hour", sa.Integer(), nullable=True),
        sa.Column("agenda_end_hour", sa.Integer(), nullable=True),
        sa.Column("consent_term_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("onboarding_dismissed_at", sa.DateTime(timezone=True), nullable=True),
    ])
    _add_missing("patients", [
        sa.Column("search_index_version", sa.Integer(), nullable=True),
    ])


def downgrade() -> None:
    op.drop_column("patients", "search_index_version")
    for column in ("onboarding_dismissed_at", "consent_term_reviewed_at", "agenda_end_hour", "agenda_start_hour"):
        op.drop_column("users", column)
