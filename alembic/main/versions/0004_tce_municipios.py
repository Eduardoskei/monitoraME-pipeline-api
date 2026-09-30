"""add TCE municipality mapping

Revision ID: 0004_tce_municipios
Revises: 0003_remove_pncp
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_tce_municipios"
down_revision = "0003_remove_pncp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tce_municipios",
        sa.Column("codigo_municipio_tce", sa.Text(), nullable=False),
        sa.Column("codigo_municipio_ibge", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("codigo_municipio_tce"),
        sa.UniqueConstraint("codigo_municipio_ibge"),
    )


def downgrade() -> None:
    op.drop_table("tce_municipios")
