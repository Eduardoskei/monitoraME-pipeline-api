"""
app/pipeline/kpis.py

Camada de agregacao (KPIs) sobre tabelas ja limpas/enriquecidas por
cleaners/ e merge.py — nao faz join nem limpeza, so agrupa e soma.

Os KPIs analíticos do TCE recebem a base canônica de empenhos líquidos criada
por ``app.pipeline.analitico``. Esta camada apenas agrupa e soma os campos
canônicos; coleta, anulações e enriquecimento são resolvidos antes dela.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pandas as pd

from app.core.config import NATUREZAS_DESPESA_MONITORADAS
from app.pipeline.cleaners.opencnpj import normalizar_porte_empresarial
from app.utils import normalizar_chave_entidade, normalizar_cnpj

class DadosInsuficientesKPI(ValueError):
    """O KPI nao pode ser calculado sem inventar ou reinterpretar dados."""


ORIGEM_FORNECEDOR_LOCAL = "Sediado no município comprador"
ORIGEM_FORNECEDOR_OUTRO_MUNICIPIO_CE = "Outro município do Ceará"
ORIGEM_FORNECEDOR_FORA_CE = "Fora do estado"
ORIGEM_FORNECEDOR_NAO_IDENTIFICADA = "Nao identificada"
ESTADO_MPE_DISPONIVEL = "DISPONIVEL"
ESTADO_MPE_INDISPONIVEL = "INDISPONIVEL"
MOTIVO_MPE_NAO_DISCRIMINADO = "MEI_NAO_DISCRIMINADO"

PORTES_EMPRESARIAIS = (
    "MEI",
    "ME",
    "EPP",
    "DEMAIS",
    "NAO_IDENTIFICADO",
)

INDICADORES_ANALITICOS_PRINCIPAIS = (
    "resumo_geral",
    "serie_mensal",
    "por_origem_geografica",
    "por_porte_fornecedor",
    "por_natureza_despesa",
    "ranking_fornecedores",
)

PORTES_OVERVIEW = ("ME", "MEI")
PORTES_COMPRAS_CONSIDERADAS_OVERVIEW = ("ME", "MEI", "EPP", "DEMAIS")

PORTES_PARTICIPACAO_OVERVIEW = (
    ("ME", ("ME",)),
    ("MEI", ("MEI",)),
    ("OUTROS_PORTES", ("EPP", "DEMAIS")),
    ("NAO_IDENTIFICADO", ("NAO_IDENTIFICADO",)),
)

DESTINOS_RECURSOS_OVERVIEW = (
    ("NO_MUNICIPIO_COMPRADOR", ORIGEM_FORNECEDOR_LOCAL),
    ("EM_OUTRO_MUNICIPIO", ORIGEM_FORNECEDOR_OUTRO_MUNICIPIO_CE),
    ("FORA_DO_CEARA", ORIGEM_FORNECEDOR_FORA_CE),
    ("ORIGEM_NAO_IDENTIFICADA", ORIGEM_FORNECEDOR_NAO_IDENTIFICADA),
)


def _validar_colunas(df: pd.DataFrame, colunas: list[str], origem: str) -> None:
    ausentes = [coluna for coluna in colunas if coluna not in df.columns]
    if ausentes:
        raise DadosInsuficientesKPI(
            f"[INDISPONIVEL] {origem}: campos necessarios nao existem: {', '.join(ausentes)}"
        )


def _soma_valor(serie: pd.Series) -> object:
    if serie.empty:
        return 0.0
    soma = serie.sum(min_count=1)
    return soma if pd.notna(soma) else pd.NA


def _percentual_valor(numerador: object, denominador: object) -> object:
    if pd.isna(numerador) or pd.isna(denominador) or float(denominador) == 0:
        return pd.NA
    return float(numerador) / float(denominador)


def _percentual_0_a_100(numerador: int, denominador: int) -> float:
    if denominador == 0:
        return 0.0
    return float(Decimal(numerador) * Decimal(100) / Decimal(denominador))


def _valor_reais_para_centavos(valor: object) -> object:
    if pd.isna(valor):
        return pd.NA

    try:
        centavos = Decimal(str(valor)) * 100
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DadosInsuficientesKPI(
            "[INDISPONIVEL] overview: valor monetario invalido."
        ) from error

    if not centavos.is_finite() or centavos != centavos.to_integral_value():
        raise DadosInsuficientesKPI(
            "[INDISPONIVEL] overview: valor monetario deve ter precisao de centavos."
        )

    inteiro = int(centavos)
    if inteiro < 0:
        raise DadosInsuficientesKPI(
            "[INDISPONIVEL] overview: empenho liquido negativo."
        )
    return inteiro


def _somar_centavos(serie: pd.Series) -> int:
    return sum(int(valor) for valor in serie.dropna())


def _booleano_estrito(serie: pd.Series) -> pd.Series:
    return serie.map(lambda valor: bool(valor) if pd.notna(valor) else False)


def _booleano_nullable(serie: pd.Series) -> pd.Series:
    return serie.map(lambda valor: bool(valor) if pd.notna(valor) else pd.NA).astype("boolean")


def _mpe_disponivel(marcadores: pd.Series) -> bool:
    return not bool(marcadores.isna().any())


def _estado_mpe(disponivel: bool) -> str:
    return ESTADO_MPE_DISPONIVEL if disponivel else ESTADO_MPE_INDISPONIVEL


def _motivo_mpe(disponivel: bool) -> str | None:
    return None if disponivel else MOTIVO_MPE_NAO_DISCRIMINADO


def _preparar_base_analitica(df: pd.DataFrame) -> pd.DataFrame:
    colunas_obrigatorias = [
        "ano_mes",
        "valor",
        "cnpj_fornecedor",
        "nome_fornecedor",
        "porte_fornecedor",
        "fornecedor_e_me",
        "fornecedor_e_mpe",
        "natureza_despesa_codigo",
        "natureza_despesa",
        "origem_geografica",
        "municipio_sede_fornecedor",
        "uf_sede_fornecedor",
    ]
    _validar_colunas(df, colunas_obrigatorias, "base analitica")

    if df.empty:
        raise DadosInsuficientesKPI("[INDISPONIVEL] base analitica sem registros.")

    base = df.copy()
    base["_valor_indicador"] = pd.to_numeric(base["valor"], errors="coerce")
    base["_fornecedor_e_me"] = _booleano_estrito(base["fornecedor_e_me"])
    base["_fornecedor_e_mpe"] = _booleano_nullable(base["fornecedor_e_mpe"])
    base["_fornecedor_local"] = base["origem_geografica"].eq(ORIGEM_FORNECEDOR_LOCAL)
    return base


def _agregar_por_grupo(
    base: pd.DataFrame,
    colunas_grupo: list[str],
    *,
    valor_total_base: object,
    ordenar_por_valor: bool = True,
) -> pd.DataFrame:
    agrupado = (
        base.groupby(colunas_grupo, dropna=False)
        .agg(
            registros=("valor", "size"),
            valor_total=("_valor_indicador", _soma_valor),
        )
        .reset_index()
    )
    agrupado["percentual_valor"] = agrupado["valor_total"].map(
        lambda valor: _percentual_valor(valor, valor_total_base)
    )

    if ordenar_por_valor:
        agrupado = (
            agrupado.assign(_valor_ordenacao=pd.to_numeric(agrupado["valor_total"], errors="coerce").fillna(-1))
            .sort_values(["_valor_ordenacao", "registros"], ascending=[False, False])
            .drop(columns=["_valor_ordenacao"])
        )
    else:
        agrupado = agrupado.sort_values(colunas_grupo, na_position="last")

    return agrupado.reset_index(drop=True)


def _soma_valor_marcado(base: pd.DataFrame, valores: pd.Series, coluna_marcador: str) -> object:
    marcador = base.loc[valores.index, coluna_marcador]
    return _soma_valor(valores[marcador])


def _soma_valor_mpe(base: pd.DataFrame, valores: pd.Series) -> object:
    marcador = base.loc[valores.index, "_fornecedor_e_mpe"]
    if not _mpe_disponivel(marcador):
        return pd.NA
    return _soma_valor(valores[marcador.fillna(False)])


def _serie_mensal(base: pd.DataFrame) -> pd.DataFrame:
    serie = (
        base.groupby("ano_mes", dropna=False)
        .agg(
            registros=("valor", "size"),
            valor_total=("_valor_indicador", _soma_valor),
            valor_me=(
                "_valor_indicador",
                lambda valores: _soma_valor_marcado(base, valores, "_fornecedor_e_me"),
            ),
            valor_mpe=(
                "_valor_indicador",
                lambda valores: _soma_valor_mpe(base, valores),
            ),
            valor_fornecedor_local=(
                "_valor_indicador",
                lambda valores: _soma_valor_marcado(base, valores, "_fornecedor_local"),
            ),
        )
        .reset_index()
        .sort_values("ano_mes", na_position="last")
        .reset_index(drop=True)
    )
    serie["percentual_me"] = serie.apply(
        lambda linha: _percentual_valor(linha["valor_me"], linha["valor_total"]),
        axis=1,
    )
    serie["percentual_mpe"] = serie.apply(
        lambda linha: _percentual_valor(linha["valor_mpe"], linha["valor_total"]),
        axis=1,
    )
    qualidade_mpe = (
        base.groupby("ano_mes", dropna=False)["_fornecedor_e_mpe"]
        .apply(_mpe_disponivel)
        .rename("mei_discriminado")
        .reset_index()
    )
    serie = serie.merge(qualidade_mpe, on="ano_mes", how="left")
    serie["estado_mpe"] = serie["mei_discriminado"].map(_estado_mpe)
    serie["motivo_mpe"] = serie["mei_discriminado"].map(_motivo_mpe)
    serie["percentual_fornecedor_local"] = serie.apply(
        lambda linha: _percentual_valor(linha["valor_fornecedor_local"], linha["valor_total"]),
        axis=1,
    )
    return serie


def calcular_indicadores_analiticos_principais(
    base_analitica: pd.DataFrame,
    *,
    limite_ranking: int = 10,
) -> dict[str, pd.DataFrame]:
    """Calcula os principais indicadores sobre a base analitica canonica.

    A funcao nao tenta descobrir colunas equivalentes: ela exige os nomes
    canonicos produzidos por ``app.pipeline.analitico``. Assim, um indicador so
    e calculado quando a base carrega explicitamente o dado necessario.
    """
    if limite_ranking < 1:
        raise ValueError("limite_ranking deve ser maior ou igual a 1.")

    base = _preparar_base_analitica(base_analitica)
    valor_total = _soma_valor(base["_valor_indicador"])
    valor_me = _soma_valor(base.loc[base["_fornecedor_e_me"], "_valor_indicador"])
    mpe_disponivel = _mpe_disponivel(base["_fornecedor_e_mpe"])
    valor_mpe = (
        _soma_valor(base.loc[base["_fornecedor_e_mpe"].fillna(False), "_valor_indicador"])
        if mpe_disponivel
        else pd.NA
    )
    valor_local = _soma_valor(base.loc[base["_fornecedor_local"], "_valor_indicador"])

    resumo_geral = pd.DataFrame(
        [
            {
                "total_registros": int(len(base)),
                "total_fornecedores": int(base["cnpj_fornecedor"].dropna().nunique()),
                "registros_sem_valor": int(base["_valor_indicador"].isna().sum()),
                "valor_total": valor_total,
                "valor_me": valor_me,
                "percentual_me": _percentual_valor(valor_me, valor_total),
                "valor_mpe": valor_mpe,
                "percentual_mpe": _percentual_valor(valor_mpe, valor_total),
                "mei_discriminado": mpe_disponivel,
                "estado_mpe": _estado_mpe(mpe_disponivel),
                "motivo_mpe": _motivo_mpe(mpe_disponivel),
                "valor_fornecedor_local": valor_local,
                "percentual_fornecedor_local": _percentual_valor(valor_local, valor_total),
            }
        ]
    )

    ranking_fornecedores = _agregar_por_grupo(
        base,
        [
            "cnpj_fornecedor",
            "nome_fornecedor",
            "porte_fornecedor",
            "municipio_sede_fornecedor",
            "uf_sede_fornecedor",
            "origem_geografica",
        ],
        valor_total_base=valor_total,
    ).head(limite_ranking)

    return {
        "resumo_geral": resumo_geral,
        "serie_mensal": _serie_mensal(base),
        "por_origem_geografica": _agregar_por_grupo(
            base,
            ["origem_geografica"],
            valor_total_base=valor_total,
        ),
        "por_porte_fornecedor": _agregar_por_grupo(
            base,
            ["porte_fornecedor"],
            valor_total_base=valor_total,
        ),
        "por_natureza_despesa": _agregar_por_grupo(
            base,
            ["natureza_despesa_codigo", "natureza_despesa"],
            valor_total_base=valor_total,
        ),
        "ranking_fornecedores": ranking_fornecedores.reset_index(drop=True),
    }


def _eh_me_estrita(valor: object) -> bool:
    """Aceita apenas classificacoes inequivocas de Microempresa (ME)."""
    if pd.isna(valor):
        return False
    chave = normalizar_chave_entidade(valor)
    return chave in {"ME", "MICROEMPRESA", "MICRO EMPRESA"}


def calcular_participacao_me_local(
    licitacoes: pd.DataFrame,
    participantes: pd.DataFrame,
    *,
    coluna_licitacao: str,
    coluna_licitacao_participante: str,
    coluna_porte: str,
    coluna_municipio_empresa: str,
    coluna_municipio_comprador: str,
    coluna_cnpj: str | None = None,
    coluna_secretaria: str | None = None,
    coluna_data: str | None = None,
) -> dict[str, object]:
    """Calcula o KPI de participacao de ME local em licitacoes unicas.

    ``participantes`` precisa ter grao de proponente/concorrente. Tabelas de
    contratos, adjudicados ou vencedores nao devem ser passadas aqui. Os nomes
    das colunas sao obrigatoriamente informados pelo chamador para que o motor
    nao tente adivinhar o significado dos dados.
    """
    if licitacoes.empty:
        raise DadosInsuficientesKPI("[INDISPONIVEL] Nenhuma licitacao real foi fornecida.")
    if participantes.empty:
        raise DadosInsuficientesKPI(
            "[INDISPONIVEL] Nao ha participantes/proponentes; vencedores nao substituem participantes."
        )

    _validar_colunas(licitacoes, [coluna_licitacao, coluna_municipio_comprador], "licitacoes")
    _validar_colunas(
        participantes,
        [coluna_licitacao_participante, coluna_porte, coluna_municipio_empresa],
        "participantes",
    )
    opcionais = [c for c in (coluna_secretaria, coluna_data) if c]
    _validar_colunas(licitacoes, opcionais, "licitacoes")
    if coluna_cnpj:
        _validar_colunas(participantes, [coluna_cnpj], "participantes")

    cols_licitacao = [coluna_licitacao, coluna_municipio_comprador, *opcionais]
    base = licitacoes[cols_licitacao].copy()
    base = base.dropna(subset=[coluna_licitacao]).drop_duplicates(subset=[coluna_licitacao])
    if base.empty:
        raise DadosInsuficientesKPI("[INDISPONIVEL] Nao ha identificadores validos de licitacao.")

    props = participantes.copy()
    props = props.dropna(subset=[coluna_licitacao_participante])
    props["_eh_me"] = props[coluna_porte].map(_eh_me_estrita)
    props["_municipio_empresa"] = props[coluna_municipio_empresa].map(normalizar_chave_entidade)
    if coluna_cnpj:
        props["_cnpj"] = props[coluna_cnpj].map(normalizar_cnpj)

    dados = base.merge(
        props,
        left_on=coluna_licitacao,
        right_on=coluna_licitacao_participante,
        how="left",
        suffixes=("_licitacao", "_participante"),
    )
    comprador = f"{coluna_municipio_comprador}_licitacao" if coluna_municipio_comprador in props.columns else coluna_municipio_comprador
    dados["_municipio_comprador"] = dados[comprador].map(normalizar_chave_entidade)
    dados["_me_local"] = (
        dados["_eh_me"].fillna(False)
        & dados["_municipio_empresa"].notna()
        & dados["_municipio_comprador"].notna()
        & dados["_municipio_empresa"].eq(dados["_municipio_comprador"])
    )
    dados["_me_externa"] = (
        dados["_eh_me"].fillna(False)
        & dados["_municipio_empresa"].notna()
        & dados["_municipio_comprador"].notna()
        & dados["_municipio_empresa"].ne(dados["_municipio_comprador"])
    )

    flags = dados.groupby(coluna_licitacao, dropna=False).agg(
        com_me_local=("_me_local", "any"), com_me_externa=("_me_externa", "any")
    )
    total = len(base)
    com_local = int(flags["com_me_local"].sum())
    com_externa = int(flags["com_me_externa"].sum())
    resumo = pd.DataFrame(
        [{
            "total_licitacoes": total,
            "licitacoes_com_me_local": com_local,
            "licitacoes_sem_me_local": total - com_local,
            "percentual_me_local": com_local / total * 100,
            "licitacoes_com_me_externa": com_externa,
            "percentual_me_externa": com_externa / total * 100,
        }]
    )

    resultado: dict[str, object] = {"resumo_geral": resumo, "dados_tratados": dados}
    for nome, coluna in (("por_secretaria", coluna_secretaria), ("historico", coluna_data)):
        if not coluna:
            continue
        agrupador = coluna
        if nome == "historico":
            dados["_periodo"] = dados[coluna].astype("string").str.slice(0, 4).where(
                dados[coluna].astype("string").str.match(r"^\d{4}")
            )
            agrupador = "_periodo"
        por_licitacao = dados.groupby([agrupador, coluna_licitacao], dropna=False)["_me_local"].any().reset_index()
        tabela = por_licitacao.groupby(agrupador, dropna=False).agg(
            total_licitacoes=(coluna_licitacao, "nunique"),
            licitacoes_com_me_local=("_me_local", "sum"),
        ).reset_index()
        tabela["percentual_me_local"] = tabela["licitacoes_com_me_local"] / tabela["total_licitacoes"] * 100
        resultado[nome] = tabela

    comparacao = pd.DataFrame([
        {"tipo": "ME Local", "licitacoes": com_local, "percentual": com_local / total * 100},
        {"tipo": "ME Externa", "licitacoes": com_externa, "percentual": com_externa / total * 100},
    ])
    resultado["me_local_externa"] = comparacao
    return resultado


def extrair_ano_mes(coluna_data: pd.Series) -> pd.Series:
    """
    Extrai o periodo 'YYYY-MM' de uma coluna de data ja normalizada para ISO
    8601 por `app.utils.converter_datas` ('YYYY-MM-DD', 'YYYY-MM-DDTHH:MM:SS'
    ou '...Z'). Valores nulos/vazios (incluindo o marcador 'nao_informado' que
    `app.utils.tratar_nulos` usa em colunas de texto) viram `None`.
    """
    texto = coluna_data.astype("string")
    ano_mes = texto.str.slice(0, 7)
    valido = texto.notna() & texto.str.match(r"^\d{4}-\d{2}")
    return ano_mes.where(valido, None)


def calcular_participacao_por_porte(
    df: pd.DataFrame,
    *,
    colunas_agrupamento: list[str],
    coluna_valor: str,
    coluna_porte: str = "fornecedor_porte_padronizado",
) -> pd.DataFrame:
    """Calcula quantidade, valor e participação por porte empresarial."""
    colunas_resultado = [
        *colunas_agrupamento,
        "porte",
        "total_empenhos",
        "quantidade_empenhos",
        "total_compras",
        "valor_porte",
        "percentual_empenhos",
        "percentual_valor",
    ]

    if not colunas_agrupamento:
        raise DadosInsuficientesKPI(
            "[INDISPONIVEL] participacao por porte: "
            "informe ao menos uma coluna de agrupamento."
        )

    if df.empty:
        return pd.DataFrame(columns=colunas_resultado)

    _validar_colunas(
        df,
        [*colunas_agrupamento, coluna_valor],
        "participacao por porte",
    )

    base = df[[*colunas_agrupamento, coluna_valor]].copy()

    if coluna_porte in df.columns:
        base[coluna_porte] = df[coluna_porte]
    else:
        base[coluna_porte] = pd.NA

    base["porte"] = (
        base[coluna_porte]
        .map(normalizar_porte_empresarial)
        .fillna("NAO_IDENTIFICADO")
    )

    totais = (
        base.groupby(colunas_agrupamento, dropna=False)
        .agg(
            total_empenhos=(coluna_valor, "size"),
            total_compras=(coluna_valor, "sum"),
        )
        .reset_index()
    )

    por_porte = (
        base.groupby(
            [*colunas_agrupamento, "porte"],
            dropna=False,
        )
        .agg(
            quantidade_empenhos=(coluna_valor, "size"),
            valor_porte=(coluna_valor, "sum"),
        )
        .reset_index()
    )

    grupos = totais[colunas_agrupamento].drop_duplicates().copy()
    categorias = pd.DataFrame({"porte": list(PORTES_EMPRESARIAIS)})

    grupos["_chave_cruzamento"] = 1
    categorias["_chave_cruzamento"] = 1

    grade = grupos.merge(
        categorias,
        on="_chave_cruzamento",
    ).drop(columns="_chave_cruzamento")

    resultado = grade.merge(
        por_porte,
        on=[*colunas_agrupamento, "porte"],
        how="left",
    )

    resultado = resultado.merge(
        totais,
        on=colunas_agrupamento,
        how="left",
    )

    resultado["quantidade_empenhos"] = (
        resultado["quantidade_empenhos"]
        .fillna(0)
        .astype(int)
    )
    resultado["valor_porte"] = resultado["valor_porte"].fillna(0)

    resultado["percentual_empenhos"] = (
        resultado["quantidade_empenhos"]
        / resultado["total_empenhos"]
    ).where(resultado["total_empenhos"] != 0)

    resultado["percentual_valor"] = (
        resultado["valor_porte"]
        / resultado["total_compras"]
    ).where(resultado["total_compras"] != 0)

    return resultado[colunas_resultado]


def calcular_participacao_por_porte_por_mes(
    df: pd.DataFrame,
    *,
    coluna_data: str,
    coluna_valor: str,
    coluna_porte: str = "fornecedor_porte_padronizado",
) -> pd.DataFrame:
    """Calcula a participação dos portes empresariais por mês."""
    if df.empty:
        return pd.DataFrame(
            columns=[
                "ano_mes",
                "porte",
                "total_empenhos",
                "quantidade_empenhos",
                "total_compras",
                "valor_porte",
                "percentual_empenhos",
                "percentual_valor",
            ]
        )

    _validar_colunas(
        df,
        [coluna_data, coluna_valor],
        "participacao por porte por mes",
    )

    base = df.copy()
    base["ano_mes"] = extrair_ano_mes(base[coluna_data])

    return calcular_participacao_por_porte(
        base,
        colunas_agrupamento=["ano_mes"],
        coluna_valor=coluna_valor,
        coluna_porte=coluna_porte,
    )


def calcular_overview_me_mei(base_analitica: pd.DataFrame) -> dict[str, object]:
    """Calcula o overview restrito a ME e MEI sobre empenhos líquidos.

    ``compras_consideradas`` representa todos os portes identificados. A
    participação compara ME + MEI contra esse total. O detalhamento por porte
    inclui ainda os valores não identificados em seu próprio denominador. Os
    indicadores geográficos continuam restritos a ME + MEI.
    """
    _validar_colunas(
        base_analitica,
        [
            "ano_mes",
            "valor",
            "porte_fornecedor",
            "origem_geografica",
            "natureza_despesa_codigo",
        ],
        "overview ME e MEI",
    )

    todos = base_analitica.copy()
    todos["_valor_centavos"] = todos["valor"].map(_valor_reais_para_centavos)
    todos["_porte_overview"] = todos["porte_fornecedor"].where(
        todos["porte_fornecedor"].isin(PORTES_COMPRAS_CONSIDERADAS_OVERVIEW),
        "NAO_IDENTIFICADO",
    )

    compras = todos.loc[
        todos["_porte_overview"].isin(
            PORTES_COMPRAS_CONSIDERADAS_OVERVIEW
        )
    ].copy()
    base = compras.loc[compras["_porte_overview"].isin(PORTES_OVERVIEW)].copy()

    compras_consideradas = _somar_centavos(compras["_valor_centavos"])
    microempresas = _somar_centavos(base["_valor_centavos"])

    elementos_despesa = [
        {
            "codigo": codigo,
            "nome": NATUREZAS_DESPESA_MONITORADAS[codigo],
            "valor_liquido_centavos": _somar_centavos(
                base.loc[
                    base["natureza_despesa_codigo"].eq(codigo),
                    "_valor_centavos",
                ]
            ),
        }
        for codigo in sorted(NATUREZAS_DESPESA_MONITORADAS)
    ]

    total_todos_portes = _somar_centavos(todos["_valor_centavos"])
    participacao_por_porte = []
    for categoria, portes in PORTES_PARTICIPACAO_OVERVIEW:
        valor_categoria = _somar_centavos(
            todos.loc[todos["_porte_overview"].isin(portes), "_valor_centavos"]
        )
        participacao_por_porte.append(
            {
                "porte": categoria,
                "valor_centavos": valor_categoria,
                "percentual": _percentual_0_a_100(
                    valor_categoria,
                    total_todos_portes,
                ),
            }
        )

    origem = base["origem_geografica"].where(
        base["origem_geografica"].isin(
            {origem_canonica for _, origem_canonica in DESTINOS_RECURSOS_OVERVIEW}
        ),
        ORIGEM_FORNECEDOR_NAO_IDENTIFICADA,
    )
    base["_origem_overview"] = origem.fillna(ORIGEM_FORNECEDOR_NAO_IDENTIFICADA)

    por_origem = {
        origem_canonica: _somar_centavos(
            base.loc[base["_origem_overview"].eq(origem_canonica), "_valor_centavos"]
        )
        for _, origem_canonica in DESTINOS_RECURSOS_OVERVIEW
    }
    valor_local = por_origem[ORIGEM_FORNECEDOR_LOCAL]
    valor_fora_municipio = (
        por_origem[ORIGEM_FORNECEDOR_OUTRO_MUNICIPIO_CE]
        + por_origem[ORIGEM_FORNECEDOR_FORA_CE]
    )

    evolucao: list[dict[str, object]] = []
    periodos = sorted(
        str(periodo)
        for periodo in compras["ano_mes"].dropna().unique()
        if str(periodo).strip()
    )
    for periodo in periodos:
        mensal = compras.loc[compras["ano_mes"].astype("string").eq(periodo)]
        mensal_microempresas = mensal.loc[
            mensal["porte_fornecedor"].isin(PORTES_OVERVIEW)
        ]
        evolucao.append(
            {
                "periodo": periodo,
                "compras_consideradas_centavos": _somar_centavos(
                    mensal["_valor_centavos"]
                ),
                "microempresas_centavos": _somar_centavos(
                    mensal_microempresas["_valor_centavos"]
                ),
            }
        )

    destino_recursos = [
        {
            "destino": destino,
            "valor_centavos": por_origem[origem_canonica],
            "percentual": _percentual_0_a_100(
                por_origem[origem_canonica],
                microempresas,
            ),
        }
        for destino, origem_canonica in DESTINOS_RECURSOS_OVERVIEW
    ]

    return {
        "kpis": {
            "percentual_participacao_me": _percentual_0_a_100(
                microempresas,
                compras_consideradas,
            ),
            "total_compras_ME_centavos": microempresas,
            "percentual_compras_fornecedores_locais": _percentual_0_a_100(
                valor_local,
                microempresas,
            ),
            "percentual_recursos_fora_municipio": _percentual_0_a_100(
                valor_fora_municipio,
                microempresas,
            ),
        },
        "participacao_por_porte_empresarial": participacao_por_porte,
        "elementos_despesa": elementos_despesa,
        "evolucao_compras_consideradas": evolucao,
        "destino_recursos": destino_recursos,
    }
