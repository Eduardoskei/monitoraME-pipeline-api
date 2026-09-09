"""initial main schema

Revision ID: 0001_initial_main
Revises:
Create Date: 2026-09-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial_main"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pncp_pca_itens CASCADE")
    op.execute("DROP TABLE IF EXISTS pncp_pca_planos CASCADE")
    op.execute("DROP TABLE IF EXISTS pncp_contratos CASCADE")
    op.execute("DROP TABLE IF EXISTS pncp_resultados CASCADE")
    op.execute("DROP TABLE IF EXISTS pncp_itens CASCADE")
    op.execute("DROP TABLE IF EXISTS pncp_contratacoes CASCADE")
    op.execute("DROP TABLE IF EXISTS pncp_fornecedores CASCADE")
    op.execute("DROP TABLE IF EXISTS pncp_ingestion_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS pncp_ingestion_state CASCADE")
    op.execute("DROP TABLE IF EXISTS fornecedores_me CASCADE")
    op.execute("DROP TABLE IF EXISTS ibge_municipios CASCADE")
    op.execute("DROP TABLE IF EXISTS ibge_cache CASCADE")
    op.execute("DROP TABLE IF EXISTS municipio_coordenadas CASCADE")

    op.create_table(
        "ibge_municipios",
        sa.Column("codigo_municipio", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("uf", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("codigo_municipio"),
    )
    op.create_index(
        "idx_ibge_municipios_uf_nome",
        "ibge_municipios",
        ["uf", "nome"],
    )

    op.create_table(
        "fornecedores_me",
        sa.Column("cnpj", sa.Text(), nullable=False),
        sa.Column("razao_social", sa.Text(), nullable=True),
        sa.Column("porte", sa.Text(), nullable=False),
        sa.CheckConstraint("porte = 'ME'", name="ck_fornecedores_me_porte_me"),
        sa.PrimaryKeyConstraint("cnpj"),
    )

    op.create_table(
        "pncp_ingestion_state",
        sa.Column("escopo", sa.Text(), nullable=False),
        sa.Column("ultima_execucao_sucesso", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "parametros",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("escopo"),
    )

    op.create_table(
        "pncp_ingestion_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("escopo", sa.Text(), nullable=False),
        sa.Column("status_execucao", sa.Text(), nullable=False),
        sa.Column("executado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("janela_inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("janela_fim", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantidade_lida", sa.Integer(), nullable=False),
        sa.Column("quantidade_inserida", sa.Integer(), nullable=False),
        sa.Column("quantidade_atualizada", sa.Integer(), nullable=False),
        sa.Column("quantidade_com_erro", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "status_execucao IN ('sucesso', 'falha')",
            name="ck_pncp_ingestion_runs_status_execucao",
        ),
        sa.CheckConstraint("quantidade_lida >= 0", name="ck_pncp_ingestion_runs_quantidade_lida"),
        sa.CheckConstraint("quantidade_inserida >= 0", name="ck_pncp_ingestion_runs_quantidade_inserida"),
        sa.CheckConstraint("quantidade_atualizada >= 0", name="ck_pncp_ingestion_runs_quantidade_atualizada"),
        sa.CheckConstraint("quantidade_com_erro >= 0", name="ck_pncp_ingestion_runs_quantidade_com_erro"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pncp_ingestion_runs_escopo_execucao
        ON pncp_ingestion_runs (escopo, executado_em DESC)
        """
    )

    op.create_table(
        "pncp_contratacoes",
        sa.Column("numero_controle_pncp", sa.Text(), nullable=False),
        sa.Column("cnpj_orgao", sa.Text(), nullable=True),
        sa.Column("ano_compra", sa.Integer(), nullable=True),
        sa.Column("sequencial_compra", sa.Integer(), nullable=True),
        sa.Column("modalidade_id", sa.Integer(), nullable=True),
        sa.Column("modalidade_nome", sa.Text(), nullable=True),
        sa.Column("objeto_compra", sa.Text(), nullable=True),
        sa.Column("natureza_despesa_monitorada", sa.Text(), nullable=True),
        sa.Column("data_publicacao_pncp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao_global", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "inserido_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("numero_controle_pncp"),
    )
    op.create_index(
        "idx_pncp_contratacoes_datas",
        "pncp_contratacoes",
        ["data_publicacao_pncp", "data_atualizacao", "data_atualizacao_global"],
    )
    op.create_index(
        "idx_pncp_contratacoes_modalidade_uf",
        "pncp_contratacoes",
        ["modalidade_id", "cnpj_orgao"],
    )

    op.create_table(
        "pncp_itens",
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("numero_controle_pncp", sa.Text(), nullable=False),
        sa.Column("numero_item", sa.Integer(), nullable=True),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("material_ou_servico_nome", sa.Text(), nullable=True),
        sa.Column("item_categoria_id", sa.Integer(), nullable=True),
        sa.Column("item_categoria_nome", sa.Text(), nullable=True),
        sa.Column("categoria_item_catalogo_id", sa.Integer(), nullable=True),
        sa.Column("categoria_item_catalogo_nome", sa.Text(), nullable=True),
        sa.Column("classificacao_superior_codigo", sa.Text(), nullable=True),
        sa.Column("classificacao_superior_nome", sa.Text(), nullable=True),
        sa.Column("ncm_nbs_codigo", sa.Text(), nullable=True),
        sa.Column("ncm_nbs_descricao", sa.Text(), nullable=True),
        sa.Column("valor_total", sa.Numeric(), nullable=True),
        sa.Column("natureza_despesa_monitorada", sa.Text(), nullable=True),
        sa.Column("data_inclusao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "inserido_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["numero_controle_pncp"],
            ["pncp_contratacoes.numero_controle_pncp"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index(
        "idx_pncp_itens_contratacao",
        "pncp_itens",
        ["numero_controle_pncp"],
    )

    op.create_table(
        "pncp_resultados",
        sa.Column("resultado_id", sa.Text(), nullable=False),
        sa.Column("numero_controle_pncp", sa.Text(), nullable=False),
        sa.Column("numero_item", sa.Integer(), nullable=True),
        sa.Column("ni_fornecedor", sa.Text(), nullable=True),
        sa.Column("nome_razao_social_fornecedor", sa.Text(), nullable=True),
        sa.Column("porte_fornecedor_id", sa.Integer(), nullable=True),
        sa.Column("porte_fornecedor_nome", sa.Text(), nullable=True),
        sa.Column("porte_fornecedor_padronizado", sa.Text(), nullable=True),
        sa.Column("valor_total_homologado", sa.Numeric(), nullable=True),
        sa.Column("data_resultado", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_cancelamento", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_inclusao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "inserido_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["numero_controle_pncp"],
            ["pncp_contratacoes.numero_controle_pncp"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("resultado_id"),
    )
    op.create_index(
        "idx_pncp_resultados_fornecedor",
        "pncp_resultados",
        ["ni_fornecedor"],
    )

    op.create_table(
        "pncp_contratos",
        sa.Column("contrato_id", sa.Text(), nullable=False),
        sa.Column("numero_controle_pncp", sa.Text(), nullable=False),
        sa.Column("ano_contrato", sa.Integer(), nullable=True),
        sa.Column("sequencial_contrato", sa.Integer(), nullable=True),
        sa.Column("numero_contrato_empenho", sa.Text(), nullable=True),
        sa.Column("ni_fornecedor", sa.Text(), nullable=True),
        sa.Column("nome_razao_social_fornecedor", sa.Text(), nullable=True),
        sa.Column("valor_global", sa.Numeric(), nullable=True),
        sa.Column("data_assinatura", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_vigencia_inicio", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_vigencia_fim", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_publicacao_pncp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "inserido_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["numero_controle_pncp"],
            ["pncp_contratacoes.numero_controle_pncp"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("contrato_id"),
    )
    op.create_index(
        "idx_pncp_contratos_fornecedor",
        "pncp_contratos",
        ["ni_fornecedor"],
    )

    op.create_table(
        "pncp_pca_planos",
        sa.Column("pca_id", sa.Text(), nullable=False),
        sa.Column("numero_controle_pncp", sa.Text(), nullable=True),
        sa.Column("cnpj_orgao", sa.Text(), nullable=True),
        sa.Column("ano_pca", sa.Integer(), nullable=True),
        sa.Column("sequencial_pca", sa.Integer(), nullable=True),
        sa.Column("codigo_unidade", sa.Text(), nullable=True),
        sa.Column("nome_unidade", sa.Text(), nullable=True),
        sa.Column("municipio", sa.Text(), nullable=True),
        sa.Column("uf", sa.Text(), nullable=True),
        sa.Column("natureza_despesa_monitorada", sa.Text(), nullable=True),
        sa.Column("data_publicacao_pncp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao_global", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "inserido_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("pca_id"),
    )
    op.create_index(
        "idx_pncp_pca_planos_orgao_ano",
        "pncp_pca_planos",
        ["cnpj_orgao", "ano_pca"],
    )

    op.create_table(
        "pncp_pca_itens",
        sa.Column("pca_item_id", sa.Text(), nullable=False),
        sa.Column("pca_id", sa.Text(), nullable=False),
        sa.Column("numero_controle_pncp", sa.Text(), nullable=True),
        sa.Column("cnpj_orgao", sa.Text(), nullable=True),
        sa.Column("ano_pca", sa.Integer(), nullable=True),
        sa.Column("sequencial_pca", sa.Integer(), nullable=True),
        sa.Column("numero_item", sa.Integer(), nullable=True),
        sa.Column("categoria_item_pca_id", sa.Integer(), nullable=True),
        sa.Column("categoria_item_pca_nome", sa.Text(), nullable=True),
        sa.Column("classificacao_superior_codigo", sa.Text(), nullable=True),
        sa.Column("classificacao_superior_nome", sa.Text(), nullable=True),
        sa.Column("pdm_codigo", sa.Text(), nullable=True),
        sa.Column("pdm_descricao", sa.Text(), nullable=True),
        sa.Column("codigo_item", sa.Text(), nullable=True),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("unidade_fornecimento", sa.Text(), nullable=True),
        sa.Column("quantidade", sa.Numeric(), nullable=True),
        sa.Column("valor_unitario", sa.Numeric(), nullable=True),
        sa.Column("valor_total", sa.Numeric(), nullable=True),
        sa.Column("valor_orcamento_exercicio", sa.Numeric(), nullable=True),
        sa.Column("natureza_despesa_monitorada", sa.Text(), nullable=True),
        sa.Column("data_desejada", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_publicacao_pncp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_inclusao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "inserido_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pca_id"],
            ["pncp_pca_planos.pca_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("pca_item_id"),
    )
    op.create_index(
        "idx_pncp_pca_itens_pca",
        "pncp_pca_itens",
        ["pca_id"],
    )

    op.create_table(
        "pncp_fornecedores",
        sa.Column("cnpj", sa.Text(), nullable=False),
        sa.Column("razao_social", sa.Text(), nullable=True),
        sa.Column("porte", sa.Text(), nullable=True),
        sa.Column("porte_padronizado", sa.Text(), nullable=True),
        sa.Column("municipio_sede", sa.Text(), nullable=True),
        sa.Column("uf_sede", sa.Text(), nullable=True),
        sa.Column("cnae_principal_codigo", sa.Text(), nullable=True),
        sa.Column("cnae_principal_descricao", sa.Text(), nullable=True),
        sa.Column("elegivel_me", sa.Boolean(), nullable=True),
        sa.Column("cnpj_valido", sa.Boolean(), nullable=True),
        sa.Column("opencnpj_status", sa.Text(), nullable=True),
        sa.Column("optante_simples_nacional", sa.Boolean(), nullable=True),
        sa.Column("optante_mei", sa.Boolean(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "inserido_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("cnpj"),
    )


def downgrade() -> None:
    op.drop_table("pncp_fornecedores")
    op.drop_index("idx_pncp_pca_itens_pca", table_name="pncp_pca_itens")
    op.drop_table("pncp_pca_itens")
    op.drop_index("idx_pncp_pca_planos_orgao_ano", table_name="pncp_pca_planos")
    op.drop_table("pncp_pca_planos")
    op.drop_index("idx_pncp_contratos_fornecedor", table_name="pncp_contratos")
    op.drop_table("pncp_contratos")
    op.drop_index("idx_pncp_resultados_fornecedor", table_name="pncp_resultados")
    op.drop_table("pncp_resultados")
    op.drop_index("idx_pncp_itens_contratacao", table_name="pncp_itens")
    op.drop_table("pncp_itens")
    op.drop_index("idx_pncp_contratacoes_modalidade_uf", table_name="pncp_contratacoes")
    op.drop_index("idx_pncp_contratacoes_datas", table_name="pncp_contratacoes")
    op.drop_table("pncp_contratacoes")
    op.drop_index("idx_pncp_ingestion_runs_escopo_execucao", table_name="pncp_ingestion_runs")
    op.drop_table("pncp_ingestion_runs")
    op.drop_table("pncp_ingestion_state")
    op.drop_table("fornecedores_me")
    op.drop_index("idx_ibge_municipios_uf_nome", table_name="ibge_municipios")
    op.drop_table("ibge_municipios")
