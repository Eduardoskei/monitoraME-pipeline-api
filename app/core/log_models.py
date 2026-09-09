from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import LogBase


class LogIngestao(LogBase):
    __tablename__ = "logs_ingestao"
    __table_args__ = (
        CheckConstraint("status IN ('sucesso', 'falha')", name="ck_logs_ingestao_status"),
        CheckConstraint("registros_processados >= 0", name="ck_logs_ingestao_registros_processados"),
        CheckConstraint("falhas_ocorridas >= 0", name="ck_logs_ingestao_falhas_ocorridas"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fonte: Mapped[str] = mapped_column(Text)
    etapa: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    data_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_termino: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    registros_processados: Mapped[int] = mapped_column(Integer)
    falhas_ocorridas: Mapped[int] = mapped_column(Integer)
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


Index("idx_logs_ingestao_fonte_inicio", LogIngestao.fonte, LogIngestao.data_inicio.desc())
Index("idx_logs_ingestao_etapa_status", LogIngestao.etapa, LogIngestao.status)


__all__ = ["LogIngestao"]
