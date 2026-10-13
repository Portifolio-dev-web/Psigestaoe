"""Versão beta: teste grátis de dois meses também para as contas que já existem.

Revision ID: 20261013_beta_trial
Revises: 20261013_subscription_interest
Create Date: 2026-10-13

Contas novas já nascem com 60 dias de teste na versão beta (billing.TRIAL_DAYS).
Esta migração estende o teste das contas que JÁ EXISTEM para o cadastro + 60 dias
(ou TRIAL_DAYS, se definido), só se isso for depois do fim atual, e só para quem
ainda depende do teste: sem período pago, sem cortesia e com a conta aberta.
Nunca encurta um teste. Com BILLING_BETA=false, não faz nada.

Idempotente: rodar de novo não muda nada.
"""

import os
from datetime import timedelta, timezone

from alembic import op
import sqlalchemy as sa


revision = "20261013_beta_trial"
down_revision = "20261013_subscription_interest"
branch_labels = None
depends_on = None


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def upgrade() -> None:
    if os.environ.get("BILLING_BETA", "true").strip().lower() == "false":
        return
    days = int(os.environ.get("TRIAL_DAYS", "").strip() or 60)
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT user_id, created_at, trial_ends_at FROM users "
        "WHERE paid_until IS NULL AND courtesy_at IS NULL AND closed_at IS NULL AND created_at IS NOT NULL"
    )).fetchall()
    for user_id, created_at, trial_ends_at in rows:
        new_end = _utc(created_at) + timedelta(days=days)
        if trial_ends_at is None or _utc(trial_ends_at) < new_end:
            bind.execute(sa.text("UPDATE users SET trial_ends_at = :end WHERE user_id = :uid"),
                         {"end": new_end, "uid": user_id})


def downgrade() -> None:
    # Não volta o prazo: encurtar um teste já concedido seria injusto com quem usa.
    pass
