"""add supplier lookup index to published commitments

Revision ID: 0006_tce_empenhos_fornecedor_idx
Revises: 0005_fornecedores_cache
Create Date: 2026-10-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_tce_empenhos_fornecedor_idx"
down_revision = "0005_fornecedores_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_tce_empenhos_fornecedor_data",
        "tce_empenhos",
        [
            "tipo_documento_fornecedor",
            "documento_fornecedor",
            "data_empenho",
        ],
        postgresql_where=sa.text("documento_fornecedor IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_tce_empenhos_fornecedor_data",
        table_name="tce_empenhos",
    )
