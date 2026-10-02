"""Produto (UX): evolução ligada à sessão da agenda.

Revision ID: 20261002_record_session_link
Revises: 20261001_account_security
Create Date: 2026-10-02

- records.session_id: a sessão da agenda a que a evolução se refere (opcional).
  Permite à agenda e ao painel mostrar "Evolução registrada" ou oferecer
  "Registrar evolução" para cada sessão. ON DELETE SET NULL: excluir a sessão
  da agenda nunca apaga a evolução (guarda obrigatória do prontuário).

Evoluções já existentes ficam sem sessão. É idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261002_record_session_link"
down_revision = "20261001_account_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "session_id" not in {c["name"] for c in inspector.get_columns("records")}:
        op.add_column("records", sa.Column("session_id", sa.String(), nullable=True))
        op.create_foreign_key("fk_records_session_id", "records", "sessions", ["session_id"], ["id"],
                              ondelete="SET NULL")
    if "ix_records_session_id" not in {i["name"] for i in inspector.get_indexes("records")}:
        # Único: no máximo uma evolução por sessão (vários NULL continuam permitidos).
        op.create_index("ix_records_session_id", "records", ["session_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_records_session_id", table_name="records")
    op.drop_constraint("fk_records_session_id", "records", type_="foreignkey")
    op.drop_column("records", "session_id")
