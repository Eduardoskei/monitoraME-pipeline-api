from collections.abc import Callable, Iterable
from typing import Any
import time


LIMITE_REGISTROS_POR_REQUISICAO = 1000
THROTTLE_PAGINACAO_SEGUNDOS = 0.2


def validar_tamanho_pagina(
    tamanho_pagina: int,
    *,
    limite: int = LIMITE_REGISTROS_POR_REQUISICAO,
) -> int:
    if tamanho_pagina < 1:
        raise ValueError("tamanho_pagina deve ser maior ou igual a 1.")
    if tamanho_pagina > limite:
        raise ValueError(f"tamanho_pagina nao pode exceder {limite} registros por requisicao.")
    return tamanho_pagina


def listar_por_start_index(
    buscar_pagina: Callable[[int, int], Iterable[Any]],
    *,
    tamanho_pagina: int = LIMITE_REGISTROS_POR_REQUISICAO,
    start_index_inicial: int = 0,
    throttle_segundos: float = THROTTLE_PAGINACAO_SEGUNDOS,
) -> list[dict[str, Any]]:
    tamanho_pagina = validar_tamanho_pagina(tamanho_pagina)
    registros: list[dict[str, Any]] = []
    start_index = start_index_inicial

    while True:
        pagina = list(buscar_pagina(start_index, tamanho_pagina))

        if not pagina:
            break

        registros.extend(item for item in pagina if isinstance(item, dict))

        if len(pagina) < tamanho_pagina:
            break

        start_index += tamanho_pagina
        if throttle_segundos:
            time.sleep(throttle_segundos)

    return registros


def listar_por_numero_pagina(
    buscar_pagina: Callable[[int, int], Any],
    extrair_itens: Callable[[Any], Iterable[Any]],
    *,
    total_paginas: Callable[[Any], int | None] | None = None,
    tamanho_pagina: int = LIMITE_REGISTROS_POR_REQUISICAO,
    pagina_inicial: int = 1,
    max_paginas: int | None = None,
    throttle_segundos: float = THROTTLE_PAGINACAO_SEGUNDOS,
) -> list[dict[str, Any]]:
    tamanho_pagina = validar_tamanho_pagina(tamanho_pagina)
    if pagina_inicial < 1:
        raise ValueError("pagina_inicial deve ser maior ou igual a 1.")
    if max_paginas is not None and max_paginas < 1:
        raise ValueError("max_paginas deve ser maior ou igual a 1.")

    registros: list[dict[str, Any]] = []
    pagina = pagina_inicial

    while True:
        dados = buscar_pagina(pagina, tamanho_pagina)
        itens = list(extrair_itens(dados))

        if not itens:
            break

        registros.extend(item for item in itens if isinstance(item, dict))

        quantidade_paginas = total_paginas(dados) if total_paginas is not None else None
        if quantidade_paginas is not None and pagina >= quantidade_paginas:
            break

        if len(itens) < tamanho_pagina:
            break

        if max_paginas is not None and pagina >= max_paginas:
            break

        pagina += 1
        if throttle_segundos:
            time.sleep(throttle_segundos)

    return registros
