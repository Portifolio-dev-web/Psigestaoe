"""Add consent status to patients.

Revision ID: 20260917_consent
Revises:
Create Date: 2026-09-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260917_consent"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "patients",
        sa.Column("consent_terms", sa.Boolean(), nullable=True, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("patients", "consent_terms")