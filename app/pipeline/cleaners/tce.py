"""Limpeza dos dados da API TCE-CE."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.utils import limpar_generico


def limpar(registros: list[dict[str, Any]], chave_duplicata: list[str] | None = None) -> pd.DataFrame:
    """Limpa qualquer endpoint tabular do TCE-CE."""
    return limpar_generico(
        registros,
        colunas_data=None,
        chave_duplicata=chave_duplicata,
    )

__all__ = ["limpar"]
