"""Assinatura: planos, teste grátis, cortesia e somente leitura (regras em billing.py).

Revision ID: 20261008_billing
Revises: 20261006_productivity
Create Date: 2026-10-08

- users: campos da assinatura (fim do teste, cortesia, plano, situação
  informada pelo meio de pagamento, "pago até", início do atraso, cancelamento
  e os identificadores no meio de pagamento).
- billing_events: avisos do meio de pagamento já processados (idempotência).
- Contas que já existem ganham TRIAL_DAYS (14) dias de teste a partir de
  agora: ninguém cai no somente leitura no dia da atualização.

Idempotente, como as anteriores.
"""

import os
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa


revision = "20261008_billing"
down_revision = "20261006_productivity"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _add_missing(table: str, columns: list) -> None:
    existing = {c["name"] for c in _inspector().get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_missing("users", [
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("courtesy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("courtesy_reason", sa.String(), nullable=True, server_default=""),
        sa.Column("subscription_plan", sa.String(), nullable=True),
        sa.Column("subscription_status", sa.String(), nullable=True, server_default="none"),
        sa.Column("paid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("past_due_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column("billing_provider", sa.String(), nullable=True),
        sa.Column("billing_customer_id", sa.String(), nullable=True),
        sa.Column("billing_subscription_id", sa.String(), nullable=True),
        sa.Column("billing_updated_at", sa.DateTime(timezone=True), nullable=True),
    ])
    indexes = {i["name"] for i in _inspector().get_indexes("users")}
    for name, column in (("ix_users_billing_customer_id", "billing_customer_id"),
                         ("ix_users_billing_subscription_id", "billing_subscription_id")):
        if name not in indexes:
            op.create_index(name, "users", [column])

    if "billing_events" not in _inspector().get_table_names():
        op.create_table(
            "billing_events",
            sa.Column("event_id", sa.String(), primary_key=True),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_billing_events_user_id", "billing_events", ["user_id"])

    # Contas existentes: teste grátis a partir de agora (mesmo prazo de uma conta nova).
    trial_days = int(os.environ.get("TRIAL_DAYS", "") or 14)
    ends = datetime.now(timezone.utc) + timedelta(days=trial_days)
    op.get_bind().execute(sa.text("UPDATE users SET trial_ends_at = :ends WHERE trial_ends_at IS NULL"), {"ends": ends})


def downgrade() -> None:
    op.drop_index("ix_billing_events_user_id", table_name="billing_events")
    op.drop_table("billing_events")
    op.drop_index("ix_users_billing_subscription_id", table_name="users")
    op.drop_index("ix_users_billing_customer_id", table_name="users")
    for column in ("billing_updated_at", "billing_subscription_id", "billing_customer_id", "billing_provider",
                   "cancel_at_period_end", "past_due_since", "paid_until", "subscription_status",
                   "subscription_plan", "courtesy_reason", "courtesy_at", "trial_ends_at"):
        op.drop_column("users", column)
