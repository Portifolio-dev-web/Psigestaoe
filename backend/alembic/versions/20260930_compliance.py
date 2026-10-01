"""Conformidade LGPD/CFP: itens 12 a 16 da auditoria, termo de consentimento por
profissional e Política de Privacidade versionada.

Revision ID: 20260930_compliance
Revises: 20260930_user_crp
Create Date: 2026-09-30

O QUE ESTA MIGRAÇÃO FAZ (na ordem):
  1. users: colunas de aceite versionado da política (item 16), modelo do termo
     por profissional e token do webhook só como hash (item 15). Os tokens em
     texto puro existentes são convertidos em hash, então as integrações com
     Google Forms JÁ configuradas continuam funcionando, e a coluna em claro
     é apagada.
  2. patients: evidência do consentimento (cópia do texto + datas) e
     arquivamento (item 12).
  3. audit_logs: IP/navegador (item 14), remoção da ForeignKey com CASCADE
     (item 14), limpeza de nomes de pacientes gravados em texto puro nos
     detalhes (item 13) e, POR ÚLTIMO, os gatilhos que tornam a tabela
     somente de inclusão (a limpeza precisa acontecer antes da trava).

DEPOIS DESTA MIGRAÇÃO, rode UMA vez:
    python rotate_encryption_key.py            (simulação)
    python rotate_encryption_key.py --apply
para criptografar nome, contatos e observações de pacientes e os campos da
agenda que foram gravados em texto puro antes do item 13.

É idempotente: roda sem erro tanto em banco antigo quanto em banco criado
pelo create_all() já com as colunas novas.
"""

from alembic import op
import sqlalchemy as sa

from crypto import hash_token


revision = "20260930_compliance"
down_revision = "20260930_user_crp"
branch_labels = None
depends_on = None

# Texto que o sistema exibia como termo de consentimento ANTES da
# personalização por profissional. Pacientes que já tinham consentido
# recebem esta cópia como evidência, porque foi exatamente este o texto
# apresentado pelo sistema na época (com o nome fixo que existia no código).
# A data desses consentimentos antigos não foi registrada e fica em branco:
# evidência não se inventa.
LEGACY_CONSENT_TERM = (
    "TERMO DE CONSENTIMENTO LIVRE E ESCLARECIDO\n\n"
    "Declaro que fui informado(a) sobre os objetivos, procedimentos e natureza do atendimento "
    "psicológico realizado pela Psicóloga Fernanda Andrade Da Silva – CRP 06/228054, profissional "
    "regularmente inscrita e atuando conforme as normas estabelecidas pelo Conselho Federal de Psicologia "
    "e pelo Código de Ética Profissional do Psicólogo.\n\n"
    "Estou ciente de que o atendimento psicológico é pautado no respeito à dignidade e integridade humana "
    "e que todas as informações compartilhadas serão mantidas sob sigilo profissional.\n\n"
    "Compreendo que o sigilo poderá ser rompido apenas em situações previstas em lei ou diante de risco "
    "iminente à vida (do paciente ou de terceiros), bem como em casos de negligência ou abuso de incapazes.\n\n"
    "Os registros psicológicos serão armazenados de forma segura, conforme a Lei nº 13.709/2018 (LGPD).\n\n"
    "Estou ciente de que posso interromper o atendimento a qualquer momento, mediante comunicação prévia."
)

_PG_FUNCTION = (
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
    is_pg = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------ users
    _add_missing("users", [
        sa.Column("terms_version", sa.String(), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_term_body", sa.Text(), nullable=True),
        sa.Column("webhook_token_hash", sa.String(), nullable=True),
        sa.Column("webhook_token_hint", sa.String(), nullable=True, server_default=""),
        sa.Column("webhook_token_created_at", sa.DateTime(timezone=True), nullable=True),
    ])
    # Item 16: contas existentes ficam com terms_version NULL de propósito.
    # O "aceite" antigo era de um documento que não existia (e, no login
    # Google, automático); todos aceitarão a política v1 no próximo acesso.

    # Item 15: token em texto puro -> hash. Mantém as integrações funcionando.
    if "webhook_token" in _columns("users"):
        rows = bind.execute(sa.text(
            "SELECT user_id, webhook_token FROM users WHERE webhook_token IS NOT NULL AND webhook_token <> ''"
        )).fetchall()
        for user_id, token in rows:
            bind.execute(
                sa.text(
                    "UPDATE users SET webhook_token_hash = :h, webhook_token_hint = :hint, "
                    "webhook_token_created_at = CURRENT_TIMESTAMP WHERE user_id = :u"
                ),
                {"h": hash_token(token), "hint": token[-4:], "u": user_id},
            )
        op.drop_column("users", "webhook_token")
    if "ix_users_webhook_token_hash" not in _indexes("users"):
        op.create_index("ix_users_webhook_token_hash", "users", ["webhook_token_hash"], unique=True)

    # --------------------------------------------------------------- patients
    _add_missing("patients", [
        sa.Column("consent_given_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_term_snapshot", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    ])
    patients = sa.table(
        "patients",
        sa.column("consent_terms", sa.Boolean),
        sa.column("consent_term_snapshot", sa.Text),
    )
    op.execute(
        patients.update()
        .where(patients.c.consent_terms == sa.true())
        .where(patients.c.consent_term_snapshot.is_(None))
        .values(consent_term_snapshot=LEGACY_CONSENT_TERM)
    )

    # ------------------------------------------------------------- audit_logs
    _add_missing("audit_logs", [
        sa.Column("ip_address", sa.String(), nullable=True, server_default=""),
        sa.Column("user_agent", sa.String(), nullable=True, server_default=""),
    ])

    if is_pg:
        # Remove gatilhos eventualmente já criados pelo create_all(), senão a
        # limpeza abaixo seria bloqueada. Eles são recriados no final.
        op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs")
        op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")

        # Item 14: sem ForeignKey -> excluir um usuário não apaga a trilha dele.
        for fk in sa.inspect(bind).get_foreign_keys("audit_logs"):
            if fk.get("name"):
                op.drop_constraint(fk["name"], "audit_logs", type_="foreignkey")

    if "ix_audit_logs_owner_id" not in _indexes("audit_logs"):
        op.create_index("ix_audit_logs_owner_id", "audit_logs", ["owner_id"])

    # Item 13: remove nomes de pacientes gravados em texto puro nos detalhes.
    # O paciente continua identificável pelo entity_id; só sai o nome em claro.
    audit = sa.table(
        "audit_logs",
        sa.column("action", sa.String),
        sa.column("entity_type", sa.String),
        sa.column("detail", sa.Text),
    )
    scrubs = [
        (("paciente", "criar", "Integração Google Forms:%"), "Cadastro via Google Forms"),
        (("paciente", "criar", "Paciente %"), "Cadastro de paciente"),
        (("paciente", "editar", "Paciente %"), "Edição de cadastro"),
        (("agenda", "criar", "%"), "Sessão agendada"),  # o detalhe era o título digitado
    ]
    for (entity, action, pattern), replacement in scrubs:
        op.execute(
            audit.update()
            .where(audit.c.entity_type == entity)
            .where(audit.c.action == action)
            .where(audit.c.detail.like(pattern))
            .values(detail=replacement)
        )

    # Item 14: trava de somente inclusão, criada por último.
    if is_pg:
        op.execute(_PG_FUNCTION)
        op.execute(
            "CREATE TRIGGER audit_logs_no_update_delete BEFORE UPDATE OR DELETE ON audit_logs "
            "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
        )
        op.execute(
            "CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs "
            "FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_append_only()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs")
        op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
        op.execute("DROP FUNCTION IF EXISTS audit_logs_append_only()")

    op.drop_column("audit_logs", "user_agent")
    op.drop_column("audit_logs", "ip_address")
    # A ForeignKey com CASCADE da trilha NÃO é recriada de propósito, e os
    # nomes removidos dos detalhes não podem ser restaurados.

    for column in ("archived_at", "consent_term_snapshot", "consent_revoked_at", "consent_given_at"):
        op.drop_column("patients", column)

    op.drop_index("ix_users_webhook_token_hash", table_name="users")
    for column in ("webhook_token_created_at", "webhook_token_hint", "webhook_token_hash",
                   "consent_term_body", "terms_accepted_at", "terms_version"):
        op.drop_column("users", column)
    # O hash não pode ser revertido para o token: a coluna volta vazia e cada
    # profissional precisará configurar a integração com Google Forms de novo.
    op.add_column("users", sa.Column("webhook_token", sa.String(), nullable=True))
