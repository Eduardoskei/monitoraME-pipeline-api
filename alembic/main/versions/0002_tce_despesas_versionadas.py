"""versioned TCE expenses and cancellations

Revision ID: 0002_tce_despesas
Revises: 0001_initial_main
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_tce_despesas"
down_revision = "0001_initial_main"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tce_despesa_ingestion_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("codigo_municipio_tce", sa.Text(), nullable=False),
        sa.Column("competencia", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("substituido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quantidade_empenhos", sa.Integer(), nullable=False),
        sa.Column("quantidade_anulacoes", sa.Integer(), nullable=False),
        sa.Column(
            "problemas",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('EM_VALIDACAO', 'PUBLICADO', 'SUBSTITUIDO', 'REJEITADO')",
            name="ck_tce_despesa_runs_status",
        ),
        sa.CheckConstraint(
            "quantidade_empenhos >= 0",
            name="ck_tce_despesa_runs_qtd_empenhos",
        ),
        sa.CheckConstraint(
            "quantidade_anulacoes >= 0",
            name="ck_tce_despesa_runs_qtd_anulacoes",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_tce_despesa_run_publicado_competencia",
        "tce_despesa_ingestion_runs",
        ["codigo_municipio_tce", "competencia"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLICADO'"),
    )
    op.create_index(
        "idx_tce_despesa_runs_status_expiracao",
        "tce_despesa_ingestion_runs",
        ["status", "expira_em"],
    )

    op.create_table(
        "tce_empenhos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("chave_empenho", sa.Text(), nullable=False),
        sa.Column("exercicio_orcamento", sa.Integer(), nullable=False),
        sa.Column("codigo_orgao", sa.Text(), nullable=False),
        sa.Column("codigo_unidade_orcamentaria", sa.Text(), nullable=False),
        sa.Column("data_empenho", sa.Date(), nullable=False),
        sa.Column("numero_empenho", sa.Text(), nullable=False),
        sa.Column("codigo_natureza_despesa", sa.Text(), nullable=False),
        sa.Column("codigo_elemento_despesa", sa.Text(), nullable=False),
        sa.Column("natureza_considerada", sa.Boolean(), nullable=False),
        sa.Column("valor_empenhado_centavos", sa.BigInteger(), nullable=False),
        sa.Column("tipo_documento_fornecedor", sa.Text(), nullable=False),
        sa.Column("documento_fornecedor", sa.Text(), nullable=True),
        sa.Column("cnpj_fornecedor", sa.Text(), nullable=True),
        sa.Column("cpf_fornecedor", sa.Text(), nullable=True),
        sa.Column("nome_fornecedor", sa.Text(), nullable=True),
        sa.Column("municipio_fornecedor_informado", sa.Text(), nullable=True),
        sa.Column("uf_fornecedor_informada", sa.Text(), nullable=True),
        sa.Column("estado_empenho", sa.Text(), nullable=True),
        sa.Column("numero_nota_anulacao_informado", sa.Text(), nullable=True),
        sa.Column("numero_empenho_substituto", sa.Text(), nullable=True),
        sa.Column("numero_contrato", sa.Text(), nullable=True),
        sa.Column("numero_licitacao", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "valor_empenhado_centavos >= 0",
            name="ck_tce_empenhos_valor_nao_negativo",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["tce_despesa_ingestion_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "chave_empenho", name="uq_tce_empenhos_run_chave"),
    )
    op.create_index("idx_tce_empenhos_run", "tce_empenhos", ["run_id"])
    op.create_index("idx_tce_empenhos_chave", "tce_empenhos", ["chave_empenho"])
    op.create_index(
        "idx_tce_empenhos_analitico",
        "tce_empenhos",
        ["natureza_considerada", "data_empenho", "codigo_elemento_despesa"],
    )

    op.create_table(
        "tce_anulacoes_empenhos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("chave_anulacao", sa.Text(), nullable=False),
        sa.Column("chave_empenho", sa.Text(), nullable=False),
        sa.Column("data_empenho", sa.Date(), nullable=False),
        sa.Column("numero_empenho", sa.Text(), nullable=False),
        sa.Column("numero_anulacao", sa.Text(), nullable=False),
        sa.Column("data_anulacao", sa.Date(), nullable=False),
        sa.Column("modalidade_anulacao", sa.Text(), nullable=True),
        sa.Column("descricao_anulacao", sa.Text(), nullable=True),
        sa.Column("valor_anulacao_centavos", sa.BigInteger(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "valor_anulacao_centavos >= 0",
            name="ck_tce_anulacoes_valor_nao_negativo",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["tce_despesa_ingestion_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "chave_anulacao", name="uq_tce_anulacoes_run_chave"),
    )
    op.create_index("idx_tce_anulacoes_run", "tce_anulacoes_empenhos", ["run_id"])
    op.create_index(
        "idx_tce_anulacoes_chave_empenho",
        "tce_anulacoes_empenhos",
        ["chave_empenho"],
    )


def downgrade() -> None:
    op.drop_index("idx_tce_anulacoes_chave_empenho", table_name="tce_anulacoes_empenhos")
    op.drop_index("idx_tce_anulacoes_run", table_name="tce_anulacoes_empenhos")
    op.drop_table("tce_anulacoes_empenhos")
    op.drop_index("idx_tce_empenhos_analitico", table_name="tce_empenhos")
    op.drop_index("idx_tce_empenhos_chave", table_name="tce_empenhos")
    op.drop_index("idx_tce_empenhos_run", table_name="tce_empenhos")
    op.drop_table("tce_empenhos")
    op.drop_index(
        "idx_tce_despesa_runs_status_expiracao",
        table_name="tce_despesa_ingestion_runs",
    )
    op.drop_index(
        "uq_tce_despesa_run_publicado_competencia",
        table_name="tce_despesa_ingestion_runs",
    )
    op.drop_table("tce_despesa_ingestion_runs")
