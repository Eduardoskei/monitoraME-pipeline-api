"""classificacao de porte por Receita e Simples

Revision ID: 0002_porte_receita_simples
Revises: 0001_initial_main
Create Date: 2026-10-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_porte_receita_simples"
down_revision = "0001_initial_main"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pncp_fornecedores", sa.Column("cnpj_raiz", sa.Text(), nullable=True))
    op.add_column("pncp_fornecedores", sa.Column("porte_procedencia", sa.Text(), nullable=True))
    op.add_column(
        "pncp_fornecedores",
        sa.Column("mei_discriminado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute("UPDATE pncp_fornecedores SET cnpj_raiz = LEFT(cnpj, 8) WHERE LENGTH(cnpj) = 14")
    op.execute(
        "UPDATE pncp_fornecedores "
        "SET porte_procedencia = 'RETRATO_ATUAL' "
        "WHERE porte_procedencia IS NULL"
    )
    op.execute(
        "UPDATE pncp_fornecedores "
        "SET porte_padronizado = CASE "
        "WHEN optante_mei IS TRUE THEN 'MEI' "
        "WHEN porte_padronizado = 'MEI' THEN 'ME' "
        "WHEN porte_padronizado IS NULL THEN 'NAO_IDENTIFICADO' "
        "ELSE porte_padronizado END, "
        "mei_discriminado = COALESCE(optante_mei, false)"
    )
    op.create_index(
        "idx_pncp_fornecedores_cnpj_raiz",
        "pncp_fornecedores",
        ["cnpj_raiz"],
    )


def downgrade() -> None:
    op.drop_index("idx_pncp_fornecedores_cnpj_raiz", table_name="pncp_fornecedores")
    op.drop_column("pncp_fornecedores", "mei_discriminado")
    op.drop_column("pncp_fornecedores", "porte_procedencia")
    op.drop_column("pncp_fornecedores", "cnpj_raiz")
