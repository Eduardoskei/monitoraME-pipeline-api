from __future__ import annotations

import re
from typing import Any

from app.core.config import NATUREZAS_DESPESA_MONITORADAS
from app.utils import somente_digitos

_CODIGO_DESPESA_RE = re.compile(r"^[\d\s./-]+$")


def _codigo_por_digitos(digitos: str) -> str | None:
    if not digitos:
        return None

    if len(digitos) == 2:
        return digitos

    if len(digitos) >= 6:
        return digitos[4:6]

    return None


def normalizar_codigo_natureza_despesa(valor: Any) -> str | None:
    """Normaliza codigos de natureza/elemento de despesa para dois digitos.

    A descricao textual nao e usada como criterio. Em codigos completos como
    "33903900", o elemento monitorado fica sempre nos digitos 5 e 6.
    """
    if valor is None or isinstance(valor, bool):
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    if not _CODIGO_DESPESA_RE.match(texto):
        return None

    return _codigo_por_digitos(somente_digitos(texto))


def descricao_natureza_despesa(codigo: Any) -> str | None:
    codigo_normalizado = normalizar_codigo_natureza_despesa(codigo)
    if codigo_normalizado is None:
        return None
    return NATUREZAS_DESPESA_MONITORADAS.get(codigo_normalizado)


def codigo_natureza_despesa_monitorado(valor: Any) -> bool:
    codigo = normalizar_codigo_natureza_despesa(valor)
    return codigo in NATUREZAS_DESPESA_MONITORADAS if codigo is not None else False


__all__ = [
    "codigo_natureza_despesa_monitorado",
    "descricao_natureza_despesa",
    "normalizar_codigo_natureza_despesa",
]
