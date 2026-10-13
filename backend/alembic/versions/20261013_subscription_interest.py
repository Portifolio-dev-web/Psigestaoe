"""Assinatura em versão beta: interesse em assinar (contato para a equipe).

Revision ID: 20261013_subscription_interest
Revises: 20261013_intake_retention
Create Date: 2026-10-13

- subscription_interests: quem quer assinar deixa e-mail e telefone
  (criptografados); a equipe vê na tela Administração e marca como contatado.

Idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261013_subscription_interest"
down_revision = "20261013_intake_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "subscription_interests" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "subscription_interests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan", sa.String(), nullable=False),
        sa.Column("contact_email", sa.Text(), nullable=True, server_default=""),
        sa.Column("contact_phone", sa.Text(), nullable=True, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contacted_by", sa.String(), nullable=True),
    )
    op.create_index("ix_subscription_interests_user_id", "subscription_interests", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_subscription_interests_user_id", table_name="subscription_interests")
    op.drop_table("subscription_interests")
