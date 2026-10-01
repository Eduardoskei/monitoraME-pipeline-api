"""add complete supplier cache

Revision ID: 0005_fornecedores_cache
Revises: 0004_tce_municipios
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_fornecedores_cache"
down_revision = "0004_tce_municipios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fornecedores_cache",
        sa.Column("cnpj", sa.Text(), nullable=False),
        sa.Column("opencnpj_status", sa.Text(), nullable=False),
        sa.Column(
            "dados_normalizados",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("observado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultima_tentativa_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultima_tentativa_status", sa.Text(), nullable=False),
        sa.Column(
            "cache_desatualizado",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "opencnpj_status IN ('ok', 'nao_encontrado', 'indisponivel')",
            name="ck_fornecedores_cache_status",
        ),
        sa.CheckConstraint(
            "ultima_tentativa_status IN ('ok', 'nao_encontrado', 'indisponivel')",
            name="ck_fornecedores_cache_ultima_tentativa_status",
        ),
        sa.PrimaryKeyConstraint("cnpj"),
    )
    op.create_index(
        "idx_fornecedores_cache_expira_em",
        "fornecedores_cache",
        ["expira_em"],
    )


def downgrade() -> None:
    op.drop_index("idx_fornecedores_cache_expira_em", table_name="fornecedores_cache")
    op.drop_table("fornecedores_cache")
