"""Escala: índices para as consultas reais e tabela do índice cego de busca.

Revision ID: 20260930_scale_indexes
Revises: 20260930_compliance
Create Date: 2026-09-30

Cada índice corresponde a uma consulta do server.py (detalhes em models.py).
Depois desta migração, o índice de busca por nome é preenchido por
`python rebuild_search_index.py` (o prestart.py faz isso sozinho no deploy).

SOBRE BLOQUEIO: CREATE INDEX comum bloqueia ESCRITAS na tabela enquanto o
índice é construído. No volume atual (milhares de linhas) isso leva
milissegundos. Se um dia uma tabela tiver milhões de linhas, crie o índice
manualmente com CREATE INDEX CONCURRENTLY fora da transação, e só então
rode esta migração (ela pula índices que já existem).

É idempotente: roda tanto em banco antigo quanto em banco criado pelo
create_all() com os índices já presentes.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_scale_indexes"
down_revision = "20260930_compliance"
branch_labels = None
depends_on = None

# (nome, tabela, colunas): igualdade primeiro, ordenação depois, id por último.
INDEXES = [
    ("ix_user_sessions_user_id", "user_sessions", ["user_id"]),
    ("ix_patients_owner_created", "patients", ["owner_id", "created_at", "id"]),
    ("ix_records_patient_session", "records", ["patient_id", "session_datetime", "id"]),
    ("ix_records_owner_updated", "records", ["owner_id", "updated_at"]),
    ("ix_record_versions_record_id", "record_versions", ["record_id"]),
    ("ix_record_versions_patient_id", "record_versions", ["patient_id"]),
    ("ix_sessions_owner_start", "sessions", ["owner_id", "start_time"]),
    ("ix_audit_logs_owner_timestamp", "audit_logs", ["owner_id", "timestamp", "log_id"]),
]


def _indexes(table: str) -> set:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not sa.inspect(bind).has_table("patient_search_tokens"):
        op.create_table(
            "patient_search_tokens",
            sa.Column("patient_id", sa.String(), sa.ForeignKey("patients.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("token", sa.String(32), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
        )
    if "ix_patient_search_owner_token" not in _indexes("patient_search_tokens"):
        op.create_index("ix_patient_search_owner_token", "patient_search_tokens", ["owner_id", "token"])

    for name, table, columns in INDEXES:
        if name not in _indexes(table):
            op.create_index(name, table, columns)

    # O índice simples em audit_logs.owner_id (migração anterior) fica
    # redundante: o composto acima começa por owner_id e também serve para
    # a ordenação por data. Índice redundante só custa escrita e disco.
    if "ix_audit_logs_owner_id" in _indexes("audit_logs"):
        op.drop_index("ix_audit_logs_owner_id", table_name="audit_logs")

    if bind.dialect.name == "postgresql":
        # Atualiza as estatísticas para o planejador passar a usar os índices
        # novos já na primeira consulta, sem esperar o autovacuum.
        for table in {table for _, table, _ in INDEXES} | {"patient_search_tokens"}:
            op.execute(f"ANALYZE {table}")


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        if name in _indexes(table):
            op.drop_index(name, table_name=table)
    if "ix_audit_logs_owner_id" not in _indexes("audit_logs"):
        op.create_index("ix_audit_logs_owner_id", "audit_logs", ["owner_id"])
    op.drop_table("patient_search_tokens")
