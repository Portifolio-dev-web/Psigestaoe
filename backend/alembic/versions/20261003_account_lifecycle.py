"""Segurança média + backlog: ciclo de vida da conta.

Revision ID: 20261003_account_lifecycle
Revises: 20261002_record_session_link
Create Date: 2026-10-03

- users: verificação em duas etapas (mfa_*), encerramento de conta
  (closed_at, purge_after) e validade do token do webhook
  (webhook_token_expires_at, webhook_expiry_notified_at). Tokens de webhook
  que já existem ganham validade de 1 ano a partir de hoje (não param de
  funcionar de surpresa com a atualização).
- user_sessions: aparelho e IP de cada sessão (public_id, device, ip_address).
  Sessões já abertas ficam sem aparelho ("Aparelho não identificado").
- email_tokens: links de uso único enviados por e-mail (recuperação de senha
  e confirmação de cadastro). Guarda só o hash do token.
- audit_logs: o gatilho de "somente inclusão" passa a aceitar UMA exceção, o
  expurgo de uma conta encerrada depois do prazo legal (ver models.py).

Idempotente, como as anteriores.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261003_account_lifecycle"
down_revision = "20261002_record_session_link"
branch_labels = None
depends_on = None

_PG_FUNCTION_WITH_PURGE = (
    "CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$ "
    "BEGIN "
    "IF TG_OP = 'DELETE' AND current_setting('psigestao.audit_purge', true) = 'on' THEN RETURN OLD; END IF; "
    "RAISE EXCEPTION USING MESSAGE = 'audit_logs e somente inclusao (append-only): ' || TG_OP; END; "
    "$$ LANGUAGE plpgsql"
)
_PG_FUNCTION_ORIGINAL = (
    "CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION USING MESSAGE = 'audit_logs e somente inclusao (append-only): ' || TG_OP; END; "
    "$$ LANGUAGE plpgsql"
)


def _columns(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _add_missing(table: str, columns: list) -> None:
    existing = _columns(table)
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ users
    _add_missing("users", [
        sa.Column("webhook_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_expiry_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_secret", sa.Text(), nullable=True),
        sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_last_step", sa.Integer(), nullable=True),
        sa.Column("mfa_recovery_codes", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    ])
    # Tokens de webhook existentes: 1 ano a partir da atualização.
    bind.execute(sa.text(
        "UPDATE users SET webhook_token_expires_at = CURRENT_TIMESTAMP + INTERVAL '365 days' "
        "WHERE webhook_token_hash IS NOT NULL AND webhook_token_expires_at IS NULL"
    ))

    # ---------------------------------------------------------- user_sessions
    _add_missing("user_sessions", [
        sa.Column("public_id", sa.String(), nullable=True),
        sa.Column("device", sa.String(), nullable=True, server_default=""),
        sa.Column("ip_address", sa.String(), nullable=True, server_default=""),
    ])
    # Sessões já abertas ganham um identificador aleatório.
    bind.execute(sa.text(
        "UPDATE user_sessions SET public_id = 'sess_' || substr(md5(random()::text || token_hash), 1, 16) "
        "WHERE public_id IS NULL"
    ))
    if "ix_user_sessions_public_id" not in _indexes("user_sessions"):
        op.create_index("ix_user_sessions_public_id", "user_sessions", ["public_id"], unique=True)

    # ----------------------------------------------------------- email_tokens
    if not sa.inspect(bind).has_table("email_tokens"):
        op.create_table(
            "email_tokens",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("purpose", sa.String(), nullable=False),
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "ix_email_tokens_token_hash" not in _indexes("email_tokens"):
        op.create_index("ix_email_tokens_token_hash", "email_tokens", ["token_hash"], unique=True)
    if "ix_email_tokens_user_id" not in _indexes("email_tokens"):
        op.create_index("ix_email_tokens_user_id", "email_tokens", ["user_id"])
    if "ix_email_tokens_email_purpose" not in _indexes("email_tokens"):
        op.create_index("ix_email_tokens_email_purpose", "email_tokens", ["email", "purpose"])

    # ------------------------------------------------------------- audit_logs
    if bind.dialect.name == "postgresql":
        op.execute(_PG_FUNCTION_WITH_PURGE)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_PG_FUNCTION_ORIGINAL)
    op.drop_table("email_tokens")
    op.drop_index("ix_user_sessions_public_id", table_name="user_sessions")
    for column in ("ip_address", "device", "public_id"):
        op.drop_column("user_sessions", column)
    for column in ("purge_after", "closed_at", "mfa_recovery_codes", "mfa_last_step", "mfa_enabled_at",
                   "mfa_secret", "webhook_expiry_notified_at", "webhook_token_expires_at"):
        op.drop_column("users", column)
