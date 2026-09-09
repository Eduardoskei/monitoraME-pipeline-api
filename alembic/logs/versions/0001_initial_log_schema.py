"""initial log schema

Revision ID: 0001_initial_logs
Revises:
Create Date: 2026-09-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial_logs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS logs_ingestao CASCADE")

    op.create_table(
        "logs_ingestao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("etapa", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("data_inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_termino", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registros_processados", sa.Integer(), nullable=False),
        sa.Column("falhas_ocorridas", sa.Integer(), nullable=False),
        sa.Column(
            "parametros",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "totais",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('sucesso', 'falha')", name="ck_logs_ingestao_status"),
        sa.CheckConstraint(
            "registros_processados >= 0",
            name="ck_logs_ingestao_registros_processados",
        ),
        sa.CheckConstraint("falhas_ocorridas >= 0", name="ck_logs_ingestao_falhas_ocorridas"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_logs_ingestao_fonte_inicio
        ON logs_ingestao (fonte, data_inicio DESC)
        """
    )
    op.create_index(
        "idx_logs_ingestao_etapa_status",
        "logs_ingestao",
        ["etapa", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_logs_ingestao_etapa_status", table_name="logs_ingestao")
    op.drop_index("idx_logs_ingestao_fonte_inicio", table_name="logs_ingestao")
    op.drop_table("logs_ingestao")
