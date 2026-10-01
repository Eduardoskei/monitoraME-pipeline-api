"""application logs

Revision ID: 0002_application_logs
Revises: 0001_initial_logs
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_application_logs"
down_revision = "0001_initial_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "logs_aplicacao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("nivel", sa.Text(), nullable=False),
        sa.Column("logger", sa.Text(), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column(
            "contexto",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("excecao", sa.Text(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "nivel IN ('WARNING', 'ERROR', 'CRITICAL')",
            name="ck_logs_aplicacao_nivel",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_logs_aplicacao_nivel_criado
        ON logs_aplicacao (nivel, criado_em DESC)
        """
    )


def downgrade() -> None:
    op.drop_index("idx_logs_aplicacao_nivel_criado", table_name="logs_aplicacao")
    op.drop_table("logs_aplicacao")
