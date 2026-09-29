from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.orm import MainBase


class IbgeMunicipio(MainBase):
    __tablename__ = "ibge_municipios"

    codigo_municipio: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    uf: Mapped[str] = mapped_column(Text)


class FornecedorMe(MainBase):
    __tablename__ = "fornecedores_me"
    __table_args__ = (
        CheckConstraint("porte = 'ME'", name="ck_fornecedores_me_porte_me"),
    )

    cnpj: Mapped[str] = mapped_column(Text, primary_key=True)
    razao_social: Mapped[str | None] = mapped_column(Text)
    porte: Mapped[str] = mapped_column(Text)


class PncpIngestionState(MainBase):
    __tablename__ = "pncp_ingestion_state"

    escopo: Mapped[str] = mapped_column(Text, primary_key=True)
    ultima_execucao_sucesso: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parametros: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


class PncpIngestionRun(MainBase):
    __tablename__ = "pncp_ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "status_execucao IN ('sucesso', 'falha')",
            name="ck_pncp_ingestion_runs_status_execucao",
        ),
        CheckConstraint("quantidade_lida >= 0", name="ck_pncp_ingestion_runs_quantidade_lida"),
        CheckConstraint("quantidade_inserida >= 0", name="ck_pncp_ingestion_runs_quantidade_inserida"),
        CheckConstraint("quantidade_atualizada >= 0", name="ck_pncp_ingestion_runs_quantidade_atualizada"),
        CheckConstraint("quantidade_com_erro >= 0", name="ck_pncp_ingestion_runs_quantidade_com_erro"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    escopo: Mapped[str] = mapped_column(Text)
    status_execucao: Mapped[str] = mapped_column(Text)
    executado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    janela_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    janela_fim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantidade_lida: Mapped[int] = mapped_column(Integer)
    quantidade_inserida: Mapped[int] = mapped_column(Integer)
    quantidade_atualizada: Mapped[int] = mapped_column(Integer)
    quantidade_com_erro: Mapped[int] = mapped_column(Integer)
    parametros: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    totais: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    erro: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


class PncpContratacao(MainBase):
    __tablename__ = "pncp_contratacoes"

    numero_controle_pncp: Mapped[str] = mapped_column(Text, primary_key=True)
    cnpj_orgao: Mapped[str | None] = mapped_column(Text)
    ano_compra: Mapped[int | None] = mapped_column(Integer)
    sequencial_compra: Mapped[int | None] = mapped_column(Integer)
    modalidade_id: Mapped[int | None] = mapped_column(Integer)
    modalidade_nome: Mapped[str | None] = mapped_column(Text)
    objeto_compra: Mapped[str | None] = mapped_column(Text)
    natureza_despesa_monitorada: Mapped[str | None] = mapped_column(Text)
    data_publicacao_pncp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_atualizacao_global: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    inserido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    itens: Mapped[list[PncpItem]] = relationship(
        back_populates="contratacao",
        cascade="all, delete-orphan",
    )
    resultados: Mapped[list[PncpResultado]] = relationship(
        back_populates="contratacao",
        cascade="all, delete-orphan",
    )
    contratos: Mapped[list[PncpContrato]] = relationship(
        back_populates="contratacao",
        cascade="all, delete-orphan",
    )


class PncpItem(MainBase):
    __tablename__ = "pncp_itens"

    item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    numero_controle_pncp: Mapped[str] = mapped_column(
        Text,
        ForeignKey("pncp_contratacoes.numero_controle_pncp", ondelete="CASCADE"),
    )
    numero_item: Mapped[int | None] = mapped_column(Integer)
    descricao: Mapped[str | None] = mapped_column(Text)
    material_ou_servico_nome: Mapped[str | None] = mapped_column(Text)
    item_categoria_id: Mapped[int | None] = mapped_column(Integer)
    item_categoria_nome: Mapped[str | None] = mapped_column(Text)
    categoria_item_catalogo_id: Mapped[int | None] = mapped_column(Integer)
    categoria_item_catalogo_nome: Mapped[str | None] = mapped_column(Text)
    classificacao_superior_codigo: Mapped[str | None] = mapped_column(Text)
    classificacao_superior_nome: Mapped[str | None] = mapped_column(Text)
    ncm_nbs_codigo: Mapped[str | None] = mapped_column(Text)
    ncm_nbs_descricao: Mapped[str | None] = mapped_column(Text)
    valor_total: Mapped[Decimal | None] = mapped_column(Numeric)
    natureza_despesa_monitorada: Mapped[str | None] = mapped_column(Text)
    data_inclusao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    inserido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    contratacao: Mapped[PncpContratacao] = relationship(back_populates="itens")


class PncpResultado(MainBase):
    __tablename__ = "pncp_resultados"

    resultado_id: Mapped[str] = mapped_column(Text, primary_key=True)
    numero_controle_pncp: Mapped[str] = mapped_column(
        Text,
        ForeignKey("pncp_contratacoes.numero_controle_pncp", ondelete="CASCADE"),
    )
    numero_item: Mapped[int | None] = mapped_column(Integer)
    ni_fornecedor: Mapped[str | None] = mapped_column(Text)
    nome_razao_social_fornecedor: Mapped[str | None] = mapped_column(Text)
    porte_fornecedor_id: Mapped[int | None] = mapped_column(Integer)
    porte_fornecedor_nome: Mapped[str | None] = mapped_column(Text)
    porte_fornecedor_padronizado: Mapped[str | None] = mapped_column(Text)
    valor_total_homologado: Mapped[Decimal | None] = mapped_column(Numeric)
    data_resultado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_cancelamento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_inclusao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    inserido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    contratacao: Mapped[PncpContratacao] = relationship(back_populates="resultados")


class PncpContrato(MainBase):
    __tablename__ = "pncp_contratos"

    contrato_id: Mapped[str] = mapped_column(Text, primary_key=True)
    numero_controle_pncp: Mapped[str] = mapped_column(
        Text,
        ForeignKey("pncp_contratacoes.numero_controle_pncp", ondelete="CASCADE"),
    )
    ano_contrato: Mapped[int | None] = mapped_column(Integer)
    sequencial_contrato: Mapped[int | None] = mapped_column(Integer)
    numero_contrato_empenho: Mapped[str | None] = mapped_column(Text)
    ni_fornecedor: Mapped[str | None] = mapped_column(Text)
    nome_razao_social_fornecedor: Mapped[str | None] = mapped_column(Text)
    valor_global: Mapped[Decimal | None] = mapped_column(Numeric)
    data_assinatura: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_vigencia_inicio: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_vigencia_fim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_publicacao_pncp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    inserido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    contratacao: Mapped[PncpContratacao] = relationship(back_populates="contratos")


class PncpPcaPlano(MainBase):
    __tablename__ = "pncp_pca_planos"

    pca_id: Mapped[str] = mapped_column(Text, primary_key=True)
    numero_controle_pncp: Mapped[str | None] = mapped_column(Text)
    cnpj_orgao: Mapped[str | None] = mapped_column(Text)
    ano_pca: Mapped[int | None] = mapped_column(Integer)
    sequencial_pca: Mapped[int | None] = mapped_column(Integer)
    codigo_unidade: Mapped[str | None] = mapped_column(Text)
    nome_unidade: Mapped[str | None] = mapped_column(Text)
    municipio: Mapped[str | None] = mapped_column(Text)
    uf: Mapped[str | None] = mapped_column(Text)
    natureza_despesa_monitorada: Mapped[str | None] = mapped_column(Text)
    data_publicacao_pncp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_atualizacao_global: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    inserido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    itens: Mapped[list[PncpPcaItem]] = relationship(
        back_populates="plano",
        cascade="all, delete-orphan",
    )


class PncpPcaItem(MainBase):
    __tablename__ = "pncp_pca_itens"

    pca_item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    pca_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("pncp_pca_planos.pca_id", ondelete="CASCADE"),
    )
    numero_controle_pncp: Mapped[str | None] = mapped_column(Text)
    cnpj_orgao: Mapped[str | None] = mapped_column(Text)
    ano_pca: Mapped[int | None] = mapped_column(Integer)
    sequencial_pca: Mapped[int | None] = mapped_column(Integer)
    numero_item: Mapped[int | None] = mapped_column(Integer)
    categoria_item_pca_id: Mapped[int | None] = mapped_column(Integer)
    categoria_item_pca_nome: Mapped[str | None] = mapped_column(Text)
    classificacao_superior_codigo: Mapped[str | None] = mapped_column(Text)
    classificacao_superior_nome: Mapped[str | None] = mapped_column(Text)
    pdm_codigo: Mapped[str | None] = mapped_column(Text)
    pdm_descricao: Mapped[str | None] = mapped_column(Text)
    codigo_item: Mapped[str | None] = mapped_column(Text)
    descricao: Mapped[str | None] = mapped_column(Text)
    unidade_fornecimento: Mapped[str | None] = mapped_column(Text)
    quantidade: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_unitario: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_total: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_orcamento_exercicio: Mapped[Decimal | None] = mapped_column(Numeric)
    natureza_despesa_monitorada: Mapped[str | None] = mapped_column(Text)
    data_desejada: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_publicacao_pncp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_inclusao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_atualizacao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    inserido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    plano: Mapped[PncpPcaPlano] = relationship(back_populates="itens")


class PncpFornecedor(MainBase):
    __tablename__ = "pncp_fornecedores"

    cnpj: Mapped[str] = mapped_column(Text, primary_key=True)
    razao_social: Mapped[str | None] = mapped_column(Text)
    porte: Mapped[str | None] = mapped_column(Text)
    porte_padronizado: Mapped[str | None] = mapped_column(Text)
    municipio_sede: Mapped[str | None] = mapped_column(Text)
    uf_sede: Mapped[str | None] = mapped_column(Text)
    cnae_principal_codigo: Mapped[str | None] = mapped_column(Text)
    cnae_principal_descricao: Mapped[str | None] = mapped_column(Text)
    elegivel_me: Mapped[bool | None] = mapped_column(Boolean)
    cnpj_valido: Mapped[bool | None] = mapped_column(Boolean)
    opencnpj_status: Mapped[str | None] = mapped_column(Text)
    optante_simples_nacional: Mapped[bool | None] = mapped_column(Boolean)
    optante_mei: Mapped[bool | None] = mapped_column(Boolean)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    inserido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


class TceDespesaIngestionRun(MainBase):
    __tablename__ = "tce_despesa_ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('EM_VALIDACAO', 'PUBLICADO', 'SUBSTITUIDO', 'REJEITADO')",
            name="ck_tce_despesa_runs_status",
        ),
        CheckConstraint("quantidade_empenhos >= 0", name="ck_tce_despesa_runs_qtd_empenhos"),
        CheckConstraint("quantidade_anulacoes >= 0", name="ck_tce_despesa_runs_qtd_anulacoes"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    codigo_municipio_tce: Mapped[str] = mapped_column(Text)
    competencia: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    substituido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quantidade_empenhos: Mapped[int] = mapped_column(Integer, default=0)
    quantidade_anulacoes: Mapped[int] = mapped_column(Integer, default=0)
    problemas: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        server_default=text("'[]'::jsonb"),
    )
    erro: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    empenhos: Mapped[list[TceEmpenho]] = relationship(
        back_populates="lote",
        cascade="all, delete-orphan",
    )
    anulacoes: Mapped[list[TceAnulacaoEmpenho]] = relationship(
        back_populates="lote",
        cascade="all, delete-orphan",
    )


class TceEmpenho(MainBase):
    __tablename__ = "tce_empenhos"
    __table_args__ = (
        UniqueConstraint("run_id", "chave_empenho", name="uq_tce_empenhos_run_chave"),
        CheckConstraint(
            "valor_empenhado_centavos >= 0",
            name="ck_tce_empenhos_valor_nao_negativo",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tce_despesa_ingestion_runs.id", ondelete="CASCADE"),
    )
    chave_empenho: Mapped[str] = mapped_column(Text)
    exercicio_orcamento: Mapped[int] = mapped_column(Integer)
    codigo_orgao: Mapped[str] = mapped_column(Text)
    codigo_unidade_orcamentaria: Mapped[str] = mapped_column(Text)
    data_empenho: Mapped[date] = mapped_column(Date)
    numero_empenho: Mapped[str] = mapped_column(Text)
    codigo_natureza_despesa: Mapped[str] = mapped_column(Text)
    codigo_elemento_despesa: Mapped[str] = mapped_column(Text)
    natureza_considerada: Mapped[bool] = mapped_column(Boolean)
    valor_empenhado_centavos: Mapped[int] = mapped_column(BigInteger)
    tipo_documento_fornecedor: Mapped[str] = mapped_column(Text)
    documento_fornecedor: Mapped[str | None] = mapped_column(Text)
    cnpj_fornecedor: Mapped[str | None] = mapped_column(Text)
    cpf_fornecedor: Mapped[str | None] = mapped_column(Text)
    nome_fornecedor: Mapped[str | None] = mapped_column(Text)
    municipio_fornecedor_informado: Mapped[str | None] = mapped_column(Text)
    uf_fornecedor_informada: Mapped[str | None] = mapped_column(Text)
    estado_empenho: Mapped[str | None] = mapped_column(Text)
    numero_nota_anulacao_informado: Mapped[str | None] = mapped_column(Text)
    numero_empenho_substituto: Mapped[str | None] = mapped_column(Text)
    numero_contrato: Mapped[str | None] = mapped_column(Text)
    numero_licitacao: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    lote: Mapped[TceDespesaIngestionRun] = relationship(back_populates="empenhos")


class TceAnulacaoEmpenho(MainBase):
    __tablename__ = "tce_anulacoes_empenhos"
    __table_args__ = (
        UniqueConstraint("run_id", "chave_anulacao", name="uq_tce_anulacoes_run_chave"),
        CheckConstraint(
            "valor_anulacao_centavos >= 0",
            name="ck_tce_anulacoes_valor_nao_negativo",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tce_despesa_ingestion_runs.id", ondelete="CASCADE"),
    )
    chave_anulacao: Mapped[str] = mapped_column(Text)
    chave_empenho: Mapped[str] = mapped_column(Text)
    data_empenho: Mapped[date] = mapped_column(Date)
    numero_empenho: Mapped[str] = mapped_column(Text)
    numero_anulacao: Mapped[str] = mapped_column(Text)
    data_anulacao: Mapped[date] = mapped_column(Date)
    modalidade_anulacao: Mapped[str | None] = mapped_column(Text)
    descricao_anulacao: Mapped[str | None] = mapped_column(Text)
    valor_anulacao_centavos: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    lote: Mapped[TceDespesaIngestionRun] = relationship(back_populates="anulacoes")


Index("idx_ibge_municipios_uf_nome", IbgeMunicipio.uf, IbgeMunicipio.nome)
Index("idx_pncp_ingestion_runs_escopo_execucao", PncpIngestionRun.escopo, PncpIngestionRun.executado_em.desc())
Index(
    "idx_pncp_contratacoes_datas",
    PncpContratacao.data_publicacao_pncp,
    PncpContratacao.data_atualizacao,
    PncpContratacao.data_atualizacao_global,
)
Index("idx_pncp_contratacoes_modalidade_uf", PncpContratacao.modalidade_id, PncpContratacao.cnpj_orgao)
Index("idx_pncp_itens_contratacao", PncpItem.numero_controle_pncp)
Index("idx_pncp_resultados_fornecedor", PncpResultado.ni_fornecedor)
Index("idx_pncp_contratos_fornecedor", PncpContrato.ni_fornecedor)
Index("idx_pncp_pca_planos_orgao_ano", PncpPcaPlano.cnpj_orgao, PncpPcaPlano.ano_pca)
Index("idx_pncp_pca_itens_pca", PncpPcaItem.pca_id)
Index(
    "uq_tce_despesa_run_publicado_competencia",
    TceDespesaIngestionRun.codigo_municipio_tce,
    TceDespesaIngestionRun.competencia,
    unique=True,
    postgresql_where=text("status = 'PUBLICADO'"),
)
Index(
    "idx_tce_despesa_runs_status_expiracao",
    TceDespesaIngestionRun.status,
    TceDespesaIngestionRun.expira_em,
)
Index("idx_tce_empenhos_run", TceEmpenho.run_id)
Index("idx_tce_empenhos_chave", TceEmpenho.chave_empenho)
Index(
    "idx_tce_empenhos_analitico",
    TceEmpenho.natureza_considerada,
    TceEmpenho.data_empenho,
    TceEmpenho.codigo_elemento_despesa,
)
Index("idx_tce_anulacoes_run", TceAnulacaoEmpenho.run_id)
Index("idx_tce_anulacoes_chave_empenho", TceAnulacaoEmpenho.chave_empenho)


__all__ = [
    "FornecedorMe",
    "IbgeMunicipio",
    "PncpContratacao",
    "PncpContrato",
    "PncpFornecedor",
    "PncpIngestionRun",
    "PncpIngestionState",
    "PncpItem",
    "PncpPcaItem",
    "PncpPcaPlano",
    "PncpResultado",
    "TceAnulacaoEmpenho",
    "TceDespesaIngestionRun",
    "TceEmpenho",
]
