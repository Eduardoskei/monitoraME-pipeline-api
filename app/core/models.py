from __future__ import annotations

from datetime import date, datetime
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


class TceMunicipio(MainBase):
    __tablename__ = "tce_municipios"

    codigo_municipio_tce: Mapped[str] = mapped_column(Text, primary_key=True)
    codigo_municipio_ibge: Mapped[str] = mapped_column(Text, unique=True)


class FornecedorMe(MainBase):
    __tablename__ = "fornecedores_me"
    __table_args__ = (
        CheckConstraint("porte = 'ME'", name="ck_fornecedores_me_porte_me"),
    )

    cnpj: Mapped[str] = mapped_column(Text, primary_key=True)
    razao_social: Mapped[str | None] = mapped_column(Text)
    porte: Mapped[str] = mapped_column(Text)


class FornecedorCache(MainBase):
    __tablename__ = "fornecedores_cache"
    __table_args__ = (
        CheckConstraint(
            "opencnpj_status IN ('ok', 'nao_encontrado', 'indisponivel')",
            name="ck_fornecedores_cache_status",
        ),
        CheckConstraint(
            "ultima_tentativa_status IN ('ok', 'nao_encontrado', 'indisponivel')",
            name="ck_fornecedores_cache_ultima_tentativa_status",
        ),
    )

    cnpj: Mapped[str] = mapped_column(Text, primary_key=True)
    opencnpj_status: Mapped[str] = mapped_column(Text)
    dados_normalizados: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    observado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ultima_tentativa_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ultima_tentativa_status: Mapped[str] = mapped_column(Text)
    cache_desatualizado: Mapped[bool] = mapped_column(Boolean, default=False)
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
Index("idx_fornecedores_cache_expira_em", FornecedorCache.expira_em)
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
    "FornecedorCache",
    "FornecedorMe",
    "IbgeMunicipio",
    "TceAnulacaoEmpenho",
    "TceDespesaIngestionRun",
    "TceEmpenho",
    "TceMunicipio",
]
