from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable

import pandas as pd

from app.core import database
from app.core.config import (
    NATUREZAS_DESPESA_CONSIDERADAS,
    PNCP_JANELA_INICIAL_HORAS,
    PNCP_MODALIDADES_INCREMENTAIS,
    PNCP_UFS_INCREMENTAIS,
)
from app.pipeline import merge
from app.pipeline.cleaners import opencnpj as opencnpj_cleaning
from app.pipeline.cleaners import pncp as pncp_cleaning
from app.pipeline.ingestion import fornecedores, pncp
from app.pipeline.persistence import pncp as pncp_persistence
from app.utils import normalizar_texto, primeiro_valor


CONTRATACAO_CAMPOS_CONTROLE_TEMPORAL = (
    ("dataPublicacaoPncp",),
    ("dataAtualizacao",),
    ("dataAtualizacaoGlobal",),
)
PCA_CAMPOS_CONTROLE_TEMPORAL = (
    ("dataPublicacaoPncp",),
    ("dataAtualizacao",),
    ("dataAtualizacaoGlobal",),
)
CONTRATACAO_CAMPOS_NATUREZA = (
    "objeto_compra",
    "informacao_complementar",
    "informacao_complementar_compra",
)
ITEM_CAMPOS_NATUREZA = (
    "descricao",
    "item_categoria_nome",
    "categoria_item_catalogo_nome",
    "classificacao_superior_nome",
    "ncm_nbs_descricao",
    "informacao_complementar",
    "material_ou_servico_nome",
)
PCA_ITEM_CAMPOS_NATUREZA = (
    "descricao",
    "categoria_item_pca_nome",
    "classificacao_superior_nome",
    "pdm_descricao",
    "codigo_item",
)
_DATA_SOMENTE_DIA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{8}$")


@dataclass(frozen=True)
class JanelaIncremental:
    escopo: str
    inicio: datetime
    fim: datetime
    checkpoint_anterior: datetime | None


def _utc(valor: datetime) -> datetime:
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _data_api(valor: datetime) -> str:
    return _utc(valor).strftime("%Y%m%d")


def montar_escopo_pncp(
    *,
    modalidades: Iterable[int] = PNCP_MODALIDADES_INCREMENTAIS,
    ufs: Iterable[str] = PNCP_UFS_INCREMENTAIS,
) -> str:
    modalidades_normalizadas = ",".join(str(int(modalidade)) for modalidade in modalidades)
    ufs_normalizadas = ",".join(str(uf).strip().upper() for uf in ufs)
    return f"pncp:ufs={ufs_normalizadas}:modalidades={modalidades_normalizadas}"


def calcular_janela_incremental(
    *,
    escopo: str,
    agora: datetime | None = None,
    janela_inicial_horas: int = PNCP_JANELA_INICIAL_HORAS,
) -> JanelaIncremental:
    fim = _utc(agora or _agora_utc())
    checkpoint = database.buscar_checkpoint_pncp(escopo)
    inicio = _utc(checkpoint) if checkpoint is not None else fim - timedelta(hours=janela_inicial_horas)
    return JanelaIncremental(
        escopo=escopo,
        inicio=inicio,
        fim=fim,
        checkpoint_anterior=checkpoint,
    )


def _parse_data_controle(valor: Any) -> tuple[datetime | None, bool]:
    if valor is None:
        return None, False
    try:
        if bool(pd.isna(valor)):
            return None, False
    except (TypeError, ValueError):
        pass
    if valor == "":
        return None, False
    if isinstance(valor, datetime):
        return _utc(valor), False

    texto = str(valor).strip()
    if not texto:
        return None, False

    apenas_dia = bool(_DATA_SOMENTE_DIA_RE.match(texto))
    data = pd.to_datetime(texto, utc=True, errors="coerce")
    if pd.isna(data):
        return None, False
    return data.to_pydatetime(), apenas_dia


def _valor_depois_do_inicio(valor: Any, inicio: datetime) -> bool:
    data, apenas_dia = _parse_data_controle(valor)
    if data is None:
        return False
    if apenas_dia:
        return data.date() >= inicio.date()
    return data > inicio


def _registro_alterado_desde(
    registro: dict[str, Any],
    inicio: datetime,
    campos: Iterable[tuple[str, ...]],
) -> bool:
    encontrou_campo_temporal = False
    for campo in campos:
        valor = primeiro_valor(registro, (campo,))
        if valor is None:
            continue
        try:
            if bool(pd.isna(valor)):
                continue
        except (TypeError, ValueError):
            pass
        if isinstance(valor, str) and not valor.strip():
            continue
        encontrou_campo_temporal = True
        if _valor_depois_do_inicio(valor, inicio):
            return True

    return not encontrou_campo_temporal


def _chave_contratacao(registro: dict[str, Any], indice: int) -> tuple[Any, ...]:
    numero_controle = primeiro_valor(registro, (("numeroControlePNCP",), ("numero_controle_pncp",)))
    if numero_controle not in (None, ""):
        return ("numero_controle_pncp", str(numero_controle))

    identificador = pncp.extrair_identificador_compra(registro)
    if identificador is not None:
        return (
            "identificador_compra",
            identificador.cnpj_orgao,
            identificador.ano_compra,
            identificador.sequencial_compra,
        )

    return ("sem_identificador", indice)


def _chave_pca(registro: dict[str, Any], indice: int) -> tuple[Any, ...]:
    numero_controle = primeiro_valor(registro, (("numeroControlePNCP",), ("numero_controle_pncp",)))
    if numero_controle not in (None, ""):
        return ("numero_controle_pncp", str(numero_controle))

    identificador = pncp.extrair_identificador_pca(registro)
    if identificador is not None:
        return (
            "identificador_pca",
            identificador.cnpj_orgao,
            identificador.ano_pca,
            identificador.sequencial_pca,
        )

    return ("sem_identificador", indice)


def _deduplicar(
    registros: Iterable[dict[str, Any]],
    chave: Any,
) -> list[dict[str, Any]]:
    deduplicados: dict[tuple[Any, ...], dict[str, Any]] = {}
    for indice, registro in enumerate(registros):
        deduplicados[chave(registro, indice)] = registro
    return list(deduplicados.values())


def _registro_uf(registro: dict[str, Any]) -> str | None:
    valor = primeiro_valor(
        registro,
        (
            ("uf",),
            ("ufSigla",),
            ("unidadeOrgao", "ufSigla"),
            ("unidadeOrgao", "uf"),
            ("unidade", "uf"),
            ("unidadeResponsavel", "uf"),
        ),
    )
    if valor in (None, ""):
        return None
    return str(valor).strip().upper()


def _registro_em_ufs(registro: dict[str, Any], ufs: Iterable[str]) -> bool:
    ufs_normalizadas = {str(uf).strip().upper() for uf in ufs if str(uf).strip()}
    if not ufs_normalizadas:
        return True
    uf = _registro_uf(registro)
    return uf in ufs_normalizadas if uf else False


def _coletar_contratacoes(
    janela: JanelaIncremental,
    *,
    modalidades: tuple[int, ...],
    ufs: tuple[str, ...],
    max_paginas: int | None,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    data_inicial = _data_api(janela.inicio)
    data_final = _data_api(janela.fim)
    registros: list[dict[str, Any]] = []
    totais_brutos = {
        "contratacoes_publicadas": 0,
        "contratacoes_atualizadas": 0,
    }

    for uf in ufs:
        for modalidade_id in modalidades:
            publicadas = pncp.buscar_contratacoes_publicadas(
                data_inicial,
                data_final,
                modalidade_id=modalidade_id,
                uf=uf,
                max_paginas=max_paginas,
            )
            atualizadas = pncp.buscar_contratacoes_atualizadas(
                data_inicial,
                data_final,
                modalidade_id=modalidade_id,
                uf=uf,
                max_paginas=max_paginas,
            )
            totais_brutos["contratacoes_publicadas"] += len(publicadas)
            totais_brutos["contratacoes_atualizadas"] += len(atualizadas)
            registros.extend(publicadas)
            registros.extend(atualizadas)

    recentes = [
        registro
        for registro in registros
        if _registro_alterado_desde(registro, janela.inicio, CONTRATACAO_CAMPOS_CONTROLE_TEMPORAL)
    ]
    completos = [pncp.montar_registro_compra_completo(registro) for registro in _deduplicar(recentes, _chave_contratacao)]
    return completos, len(registros), totais_brutos


def _montar_registro_item_pca(
    *,
    item: dict[str, Any],
    plano: dict[str, Any],
    identificador: pncp.IdentificadorPca,
) -> dict[str, Any]:
    numero_controle = primeiro_valor(
        plano,
        (("numeroControlePNCP",), ("numero_controle_pncp",)),
    ) or primeiro_valor(item, (("numeroControlePNCP",),))
    return {
        **item,
        "cnpj": identificador.cnpj_orgao,
        "anoPca": identificador.ano_pca,
        "sequencialPca": identificador.sequencial_pca,
        "numeroControlePNCP": numero_controle,
    }


def _coletar_pca(
    janela: JanelaIncremental,
    *,
    ufs: tuple[str, ...],
    max_paginas: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, dict[str, int]]:
    data_inicial = _data_api(janela.inicio)
    data_final = _data_api(janela.fim)
    brutos = pncp.buscar_pcas_atualizados(data_inicial, data_final, max_paginas=max_paginas)
    recentes = [
        registro
        for registro in brutos
        if _registro_alterado_desde(registro, janela.inicio, PCA_CAMPOS_CONTROLE_TEMPORAL)
    ]

    planos: list[dict[str, Any]] = []
    itens: list[dict[str, Any]] = []
    totais = {
        "pcas_atualizados": len(brutos),
        "pcas_sem_identificador": 0,
        "pcas_fora_escopo_uf": 0,
        "pca_itens": 0,
    }

    for resumo in _deduplicar(recentes, _chave_pca):
        identificador = pncp.extrair_identificador_pca(resumo)
        if identificador is None:
            totais["pcas_sem_identificador"] += 1
            continue

        detalhe = pncp.consultar_pca_consolidado(
            identificador.cnpj_orgao,
            identificador.ano_pca,
            identificador.sequencial_pca,
        )
        plano = {**resumo, **detalhe}
        if not _registro_em_ufs(plano, ufs):
            totais["pcas_fora_escopo_uf"] += 1
            continue

        planos.append(plano)
        itens_plano = pncp.consultar_itens_pca(
            identificador.cnpj_orgao,
            identificador.ano_pca,
            identificador.sequencial_pca,
        )
        totais["pca_itens"] += len(itens_plano)
        itens.extend(
            _montar_registro_item_pca(item=item, plano=plano, identificador=identificador)
            for item in itens_plano
            if isinstance(item, dict)
        )

    return planos, itens, len(brutos) + totais["pca_itens"], totais


def _naturezas_normalizadas() -> list[tuple[str, str]]:
    return [
        (natureza, normalizar_texto(natureza))
        for natureza in NATUREZAS_DESPESA_CONSIDERADAS
        if normalizar_texto(natureza)
    ]


def _linha_preenchida(valor: Any) -> bool:
    if valor is None:
        return False
    try:
        if bool(pd.isna(valor)):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto or texto.lower() == "nao_informado":
            return False
    return True


def _classificar_linha(
    linha: pd.Series,
    colunas: Iterable[str],
    naturezas: list[tuple[str, str]],
) -> tuple[str | None, str | None]:
    for coluna in colunas:
        if coluna not in linha:
            continue
        texto = normalizar_texto(linha[coluna])
        if not texto:
            continue
        for natureza_original, natureza_normalizada in naturezas:
            if natureza_normalizada in texto:
                return natureza_original, coluna
    return None, None


def _classificar_dataframe(df: pd.DataFrame | None, colunas: Iterable[str]) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()

    df = df.copy()
    naturezas = _naturezas_normalizadas()
    if df.empty:
        df["natureza_despesa_monitorada"] = pd.Series(dtype="string")
        df["natureza_despesa_match_campo"] = pd.Series(dtype="string")
        return df

    classificacoes = df.apply(lambda linha: _classificar_linha(linha, colunas, naturezas), axis=1)
    df["natureza_despesa_monitorada"] = [natureza for natureza, _campo in classificacoes]
    df["natureza_despesa_match_campo"] = [campo for _natureza, campo in classificacoes]
    return df


def _mascara_natureza(df: pd.DataFrame) -> pd.Series:
    if df.empty or "natureza_despesa_monitorada" not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    return df["natureza_despesa_monitorada"].map(_linha_preenchida)


def _filtro_coluna(df: pd.DataFrame, coluna: str, valores: set[Any]) -> pd.DataFrame:
    if df.empty or coluna not in df.columns:
        return df
    return df[df[coluna].isin(valores)].reset_index(drop=True)


def _pares_itens(df: pd.DataFrame) -> set[tuple[Any, Any]]:
    if df.empty or "numero_controle_pncp" not in df.columns or "numero_item" not in df.columns:
        return set()
    return set(zip(df["numero_controle_pncp"], df["numero_item"]))


def _filtrar_contratacoes_por_natureza(tabelas: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    contratacoes = _classificar_dataframe(tabelas.get("contratacoes"), CONTRATACAO_CAMPOS_NATUREZA)
    itens = _classificar_dataframe(tabelas.get("itens"), ITEM_CAMPOS_NATUREZA)
    resultados = tabelas.get("resultados", pd.DataFrame()).copy()
    contratos = tabelas.get("contratos", pd.DataFrame()).copy()

    controles_pai = set(contratacoes.loc[_mascara_natureza(contratacoes), "numero_controle_pncp"]) if (
        not contratacoes.empty and "numero_controle_pncp" in contratacoes.columns
    ) else set()
    itens_monitorados = itens.loc[_mascara_natureza(itens)] if not itens.empty else itens
    controles_itens = set(itens_monitorados["numero_controle_pncp"]) if (
        not itens_monitorados.empty and "numero_controle_pncp" in itens_monitorados.columns
    ) else set()
    controles = controles_pai | controles_itens

    if not controles:
        vazio = contratacoes.iloc[0:0].copy()
        return {
            "contratacoes": vazio,
            "itens": itens.iloc[0:0].copy(),
            "resultados": resultados.iloc[0:0].copy(),
            "contratos": contratos.iloc[0:0].copy(),
        }

    contratacoes = _filtro_coluna(contratacoes, "numero_controle_pncp", controles)

    if not itens.empty and "numero_controle_pncp" in itens.columns:
        mascara_item = itens["numero_controle_pncp"].isin(controles_pai) | _mascara_natureza(itens)
        itens = itens[mascara_item].reset_index(drop=True)

    pares_monitorados = _pares_itens(itens)
    if not resultados.empty and "numero_controle_pncp" in resultados.columns:
        mascara_resultados = resultados["numero_controle_pncp"].isin(controles_pai)
        if pares_monitorados and "numero_item" in resultados.columns:
            mascara_resultados = mascara_resultados | resultados.apply(
                lambda linha: (linha["numero_controle_pncp"], linha["numero_item"]) in pares_monitorados,
                axis=1,
            )
        resultados = resultados[mascara_resultados].reset_index(drop=True)

    contratos = _filtro_coluna(contratos, "numero_controle_pncp", controles)
    return {
        "contratacoes": contratacoes,
        "itens": itens,
        "resultados": resultados,
        "contratos": contratos,
    }


def _pca_id_linha(linha: pd.Series) -> str:
    numero_controle = linha.get("numero_controle_pncp")
    if _linha_preenchida(numero_controle):
        return str(numero_controle)
    partes = [linha.get("cnpj"), linha.get("ano_pca"), linha.get("sequencial_pca")]
    sufixo = ":".join(str(parte) for parte in partes if _linha_preenchida(parte))
    return f"pncp-pca:{sufixo or linha.name}"


def _adicionar_pca_id(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    df = df.copy()
    if df.empty:
        df["pca_id"] = pd.Series(dtype="string")
        return df
    df["pca_id"] = df.apply(_pca_id_linha, axis=1)
    return df


def _filtrar_pca_por_natureza(
    planos: pd.DataFrame,
    itens: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    planos = _adicionar_pca_id(_classificar_dataframe(planos, ()))
    itens = _adicionar_pca_id(_classificar_dataframe(itens, PCA_ITEM_CAMPOS_NATUREZA))

    pcas_monitorados = set(itens.loc[_mascara_natureza(itens), "pca_id"]) if (
        not itens.empty and "pca_id" in itens.columns
    ) else set()
    if not pcas_monitorados:
        return planos.iloc[0:0].copy(), itens.iloc[0:0].copy()

    return (
        _filtro_coluna(planos, "pca_id", pcas_monitorados),
        _filtro_coluna(itens, "pca_id", pcas_monitorados),
    )


def _coletar_fornecedores(tabelas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    series = []
    for nome_tabela in ("resultados", "contratos"):
        tabela = tabelas.get(nome_tabela)
        if tabela is not None and not tabela.empty and "ni_fornecedor" in tabela.columns:
            series.append(tabela["ni_fornecedor"])

    cnpjs = merge.extrair_cnpjs_distintos(*series)
    if not cnpjs:
        return pd.DataFrame()

    registros = fornecedores.coletar_fornecedores_em_lote(cnpjs)
    return opencnpj_cleaning.limpar_fornecedores(registros)


def _preparar_tabelas(
    *,
    contratacoes: list[dict[str, Any]],
    pca_planos: list[dict[str, Any]],
    pca_itens: list[dict[str, Any]],
) -> dict[str, pd.DataFrame]:
    tabelas_contratacoes = _filtrar_contratacoes_por_natureza(
        pncp_cleaning.limpar_contratacoes(contratacoes)
    )
    planos_df = pncp_cleaning.limpar_pca_planos(pca_planos)
    pca_itens_df = pncp_cleaning.limpar_pca_itens(pca_itens)
    planos_df, pca_itens_df = _filtrar_pca_por_natureza(planos_df, pca_itens_df)

    tabelas = {
        **tabelas_contratacoes,
        "pca_planos": planos_df,
        "pca_itens": pca_itens_df,
    }
    tabelas["fornecedores"] = _coletar_fornecedores(tabelas)
    return tabelas


def _totais_tabelas(tabelas: dict[str, pd.DataFrame]) -> dict[str, int]:
    return {nome: int(len(tabela)) for nome, tabela in tabelas.items()}


def executar_ingestao_incremental_pncp(
    *,
    modalidades: Iterable[int] = PNCP_MODALIDADES_INCREMENTAIS,
    ufs: Iterable[str] = PNCP_UFS_INCREMENTAIS,
    agora: datetime | None = None,
    janela_inicial_horas: int = PNCP_JANELA_INICIAL_HORAS,
    max_paginas: int | None = None,
) -> dict[str, Any]:
    modalidades_tuple = tuple(dict.fromkeys(int(modalidade) for modalidade in modalidades))
    ufs_tuple = tuple(dict.fromkeys(str(uf).strip().upper() for uf in ufs if str(uf).strip()))
    escopo = montar_escopo_pncp(modalidades=modalidades_tuple, ufs=ufs_tuple)
    janela = calcular_janela_incremental(
        escopo=escopo,
        agora=agora,
        janela_inicial_horas=janela_inicial_horas,
    )
    parametros = {
        "modalidades": modalidades_tuple,
        "ufs": ufs_tuple,
        "janela_inicial_horas": janela_inicial_horas,
        "max_paginas": max_paginas,
        "campos_controle_temporal": {
            "contratacoes": ["dataPublicacaoPncp", "dataAtualizacao", "dataAtualizacaoGlobal"],
            "pca": ["dataPublicacaoPncp", "dataAtualizacao", "dataAtualizacaoGlobal"],
        },
        "naturezas_despesa_consideradas": NATUREZAS_DESPESA_CONSIDERADAS,
    }
    executado_em = _agora_utc()
    quantidade_lida = 0
    totais_brutos: dict[str, int] = {}

    try:
        contratacoes, lidas_contratacoes, totais_contratacoes = _coletar_contratacoes(
            janela,
            modalidades=modalidades_tuple,
            ufs=ufs_tuple,
            max_paginas=max_paginas,
        )
        pca_planos, pca_itens, lidas_pca, totais_pca = _coletar_pca(
            janela,
            ufs=ufs_tuple,
            max_paginas=max_paginas,
        )
        quantidade_lida = lidas_contratacoes + lidas_pca
        totais_brutos = {**totais_contratacoes, **totais_pca}
        tabelas = _preparar_tabelas(
            contratacoes=contratacoes,
            pca_planos=pca_planos,
            pca_itens=pca_itens,
        )
        totais_processados = _totais_tabelas(tabelas)
        persistencia = pncp_persistence.persistir_tabelas(tabelas)
        totais = {
            "brutos": totais_brutos,
            "processados": totais_processados,
            "persistencia_por_tabela": persistencia.por_tabela,
        }

        database.registrar_sucesso_e_checkpoint_pncp(
            escopo=escopo,
            executado_em=executado_em,
            janela_inicio=janela.inicio,
            janela_fim=janela.fim,
            quantidade_lida=quantidade_lida,
            quantidade_inserida=persistencia.inseridos,
            quantidade_atualizada=persistencia.atualizados,
            parametros=parametros,
            totais=totais,
        )

        return {
            "fonte": "PNCP",
            "escopo": escopo,
            "status_execucao": "sucesso",
            "executado_em": executado_em.isoformat(),
            "janela_inicio": janela.inicio.isoformat(),
            "janela_fim": janela.fim.isoformat(),
            "ultima_execucao_anterior": (
                _utc(janela.checkpoint_anterior).isoformat()
                if janela.checkpoint_anterior is not None
                else None
            ),
            "quantidade_lida": quantidade_lida,
            "quantidade_inserida": persistencia.inseridos,
            "quantidade_atualizada": persistencia.atualizados,
            "quantidade_com_erro": 0,
            "totais": totais,
        }
    except Exception as error:
        database.registrar_execucao_pncp(
            escopo=escopo,
            status_execucao="falha",
            executado_em=executado_em,
            janela_inicio=janela.inicio,
            janela_fim=janela.fim,
            quantidade_lida=quantidade_lida,
            quantidade_inserida=0,
            quantidade_atualizada=0,
            quantidade_com_erro=1,
            parametros=parametros,
            totais={"brutos": totais_brutos},
            erro=str(error),
        )
        raise


__all__ = [
    "JanelaIncremental",
    "calcular_janela_incremental",
    "executar_ingestao_incremental_pncp",
    "montar_escopo_pncp",
]
