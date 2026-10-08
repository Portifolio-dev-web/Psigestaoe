"""Cadastro pelo paciente (link temporário) no lugar da integração com o Google Forms.

Revision ID: 20261009_patient_intake
Revises: 20261008_billing
Create Date: 2026-10-09

- patient_intakes: links de cadastro (só o HASH do link e do segredo do
  navegador; rascunho do paciente e "para quem é" gravados criptografados pela
  aplicação). Regras em server.py, seção "link de cadastro".
- users.webhook_token_*: REMOVIDAS. A integração com o Google Forms saiu do
  sistema; apagar as colunas invalida todos os tokens que ainda existiam (um
  formulário antigo passa a receber 404, sem gravar nada).

Idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_patient_intake"
down_revision = "20261008_billing"
branch_labels = None
depends_on = None

WEBHOOK_COLUMNS = ("webhook_token_hash", "webhook_token_hint", "webhook_token_created_at",
                   "webhook_token_expires_at", "webhook_expiry_notified_at")


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    if "patient_intakes" not in _inspector().get_table_names():
        op.create_table(
            "patient_intakes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("label", sa.Text(), nullable=True, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("extensions", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("fill_secret_hash", sa.String(), nullable=True),
            sa.Column("draft", sa.Text(), nullable=True, server_default=""),
            sa.Column("draft_saved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_by", sa.String(), nullable=True),
            sa.Column("patient_id", sa.String(), sa.ForeignKey("patients.id", ondelete="SET NULL"), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_patient_intakes_token_hash", "patient_intakes", ["token_hash"], unique=True)
        op.create_index("ix_patient_intakes_owner_created", "patient_intakes", ["owner_id", "created_at"])

    existing = {c["name"] for c in _inspector().get_columns("users")}
    indexes = {i["name"] for i in _inspector().get_indexes("users")}
    if "ix_users_webhook_token_hash" in indexes:
        op.drop_index("ix_users_webhook_token_hash", table_name="users")
    for column in WEBHOOK_COLUMNS:
        if column in existing:
            op.drop_column("users", column)


def downgrade() -> None:
    # As colunas voltam vazias: os tokens antigos não podem (nem devem) ser restaurados.
    op.add_column("users", sa.Column("webhook_token_hash", sa.String(), nullable=True))
    op.add_column("users", sa.Column("webhook_token_hint", sa.String(), nullable=True, server_default=""))
    op.add_column("users", sa.Column("webhook_token_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("webhook_token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("webhook_expiry_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_webhook_token_hash", "users", ["webhook_token_hash"], unique=True)
    op.drop_index("ix_patient_intakes_owner_created", table_name="patient_intakes")
    op.drop_index("ix_patient_intakes_token_hash", table_name="patient_intakes")
    op.drop_table("patient_intakes")
