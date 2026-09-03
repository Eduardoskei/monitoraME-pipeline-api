from typing import Any
import pandas as pd
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, MODALIDADE_ID_PADRAO, UF_PADRAO
from app.pipeline import cleaning, kpis, merge
from app.pipeline.ingestion import fornecedores, ibge, pncp, tce


def valor_json(valor: Any) -> Any:
    """Converte valores pandas/numpy em tipos aceitos por JSON."""
    if valor is None:
        return None

    try:
        nulo = pd.isna(valor)
    except (TypeError, ValueError):
        nulo = False

    try:
        if not isinstance(nulo, (list, tuple)) and not getattr(nulo, "shape", None) and bool(nulo):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(valor, pd.Timestamp):
        return valor.isoformat()

    if isinstance(valor, dict):
        return {str(chave): valor_json(item) for chave, item in valor.items()}

    if isinstance(valor, (list, tuple)):
        return [valor_json(item) for item in valor]

    if hasattr(valor, "item") and not isinstance(valor, (str, bytes, bytearray)):
        try:
            return valor.item()
        except (AttributeError, TypeError, ValueError):
            return valor

    return valor


def dataframe_para_registros(df: pd.DataFrame | None, *, limite: int | None = None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    if limite is not None:
        df = df.head(limite)

    return [
        {str(chave): valor_json(valor) for chave, valor in registro.items()}
        for registro in df.to_dict(orient="records")
    ]


def tabelas_para_json(
    tabelas: dict[str, pd.DataFrame],
    *,
    limite: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    return {nome: dataframe_para_registros(tabela, limite=limite) for nome, tabela in tabelas.items()}


def _totais_tabelas(tabelas: dict[str, pd.DataFrame]) -> dict[str, int]:
    return {nome: int(len(tabela)) for nome, tabela in tabelas.items()}


def _contratos_pncp_completos(registros: list[dict[str, Any]]) -> pd.Series | None:
    if not any(isinstance(registro.get("contratos"), list) for registro in registros):
        return None

    return pd.Series(
        [
            contrato.get("niFornecedor")
            for registro in registros
            for contrato in registro.get("contratos", [])
            if isinstance(contrato, dict)
        ]
    )


def _montar_registro_pncp_completo(publicacao: dict[str, Any]) -> dict[str, Any]:
    identificador = pncp.extrair_identificador_compra(publicacao)
    if identificador is None:
        return dict(publicacao)

    detalhe = pncp.coletar_compra_completa(
        identificador.cnpj_orgao,
        identificador.ano_compra,
        identificador.sequencial_compra,
    )
    compra = detalhe.get("compra") if isinstance(detalhe.get("compra"), dict) else {}
    registro = {**compra, **publicacao}

    itens = []
    for item_com_resultado in detalhe.get("itens", []):
        if not isinstance(item_com_resultado, dict):
            continue
        item = item_com_resultado.get("item")
        if not isinstance(item, dict):
            continue
        resultados = item_com_resultado.get("resultados")
        itens.append({**item, "resultados": resultados if isinstance(resultados, list) else []})

    contratos = []
    for contrato_com_detalhe in detalhe.get("contratos", []):
        if not isinstance(contrato_com_detalhe, dict):
            continue
        contrato = contrato_com_detalhe.get("contrato")
        detalhe_contrato = contrato_com_detalhe.get("detalhe")
        if not isinstance(contrato, dict):
            continue
        if not isinstance(detalhe_contrato, dict):
            detalhe_contrato = {}
        contratos.append({**detalhe_contrato, **contrato})

    if itens:
        registro["itens"] = itens
    if contratos:
        registro["contratos"] = contratos

    return registro


def _coletar_fornecedores(cnpjs: list[str], throttle_segundos: float) -> pd.DataFrame | None:
    if not cnpjs:
        return None

    registros = fornecedores.coletar_fornecedores_em_lote(cnpjs, throttle_segundos=throttle_segundos)
    return cleaning.limpar_fornecedores(registros)


def montar_base_pncp(
    data_inicial: str,
    data_final: str,
    *,
    modalidade_id: int = MODALIDADE_ID_PADRAO,
    uf: str | None = UF_PADRAO,
    codigo_municipio_ibge: str | int | None = None,
    cnpj_orgao: str | None = None,
    max_paginas: int | None = 1,
    incluir_detalhes: bool = False,
    enriquecer_municipios: bool = True,
    enriquecer_fornecedores: bool = False,
    throttle_fornecedores: float = 0.3,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    publicacoes = pncp.buscar_contratacoes_publicadas(
        data_inicial,
        data_final,
        modalidade_id=modalidade_id,
        uf=uf,
        codigo_municipio_ibge=codigo_municipio_ibge,
        cnpj_orgao=cnpj_orgao,
        max_paginas=max_paginas,
    )
    registros = (
        [_montar_registro_pncp_completo(publicacao) for publicacao in publicacoes]
        if incluir_detalhes
        else publicacoes
    )

    resultados_brutos: list[dict[str, Any]] = []

    for registro in registros:
        numero_controle = registro.get("numeroControlePNCP")

        for item in registro.get("itens", []):
            if not isinstance(item, dict):
                continue

            numero_item = item.get("numeroItem")
            resultados_item = item.pop("resultados", [])

            if not isinstance(resultados_item, list):
                continue

            for resultado in resultados_item:
                if isinstance(resultado, dict):
                    resultados_brutos.append(
                        {
                            **resultado,
                            "numeroControlePNCP": numero_controle,
                            "numeroItem": numero_item,
                        }
                    )

    tabelas = cleaning.limpar_pncp_contratacoes(registros)

    if resultados_brutos:
        tabelas["resultados"] = cleaning.limpar_pncp_resultados(
            resultados_brutos
        )

    municipios_df = None
    if enriquecer_municipios:
        municipios_df = cleaning.limpar_ibge_municipios(ibge.listar_municipios(uf or UF_PADRAO))

    fornecedores_df = None
    if enriquecer_fornecedores:
        contratos = tabelas.get("contratos")
        serie_cnpj = contratos["ni_fornecedor"] if contratos is not None and "ni_fornecedor" in contratos else None
        cnpjs = merge.extrair_cnpjs_distintos(serie_cnpj, _contratos_pncp_completos(registros))
        fornecedores_df = _coletar_fornecedores(cnpjs, throttle_fornecedores)

    tabelas = merge.montar_base_pncp(
        tabelas,
        fornecedores_df=fornecedores_df,
        municipios_ibge=municipios_df,
    )

    metadados = {
        "fonte": "PNCP",
        "parametros": {
            "data_inicial": data_inicial,
            "data_final": data_final,
            "modalidade_id": modalidade_id,
            "uf": uf,
            "codigo_municipio_ibge": codigo_municipio_ibge,
            "cnpj_orgao": cnpj_orgao,
            "max_paginas": max_paginas,
            "incluir_detalhes": incluir_detalhes,
            "enriquecer_municipios": enriquecer_municipios,
            "enriquecer_fornecedores": enriquecer_fornecedores,
        },
        "totais_brutos": {"publicacoes": len(publicacoes)},
    }
    return tabelas, metadados


def consultar_pncp_contratacoes(*, limite: int | None = 100, **kwargs: Any) -> dict[str, Any]:
    tabelas, metadados = montar_base_pncp(**kwargs)
    return {
        **metadados,
        "limite_resposta": limite,
        "totais": _totais_tabelas(tabelas),
        "tabelas": tabelas_para_json(tabelas, limite=limite),
    }


def montar_base_tce_contratos(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    enriquecer_fornecedores: bool = False,
    throttle_fornecedores: float = 0.3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    contratos_brutos = tce.buscar_contratos(data_inicial, data_final, codigo_municipio=codigo_municipio)
    contratados_brutos = tce.buscar_contratados(data_inicial, data_final, codigo_municipio=codigo_municipio)

    df_contratos = cleaning.limpar_tce(contratos_brutos, chave_duplicata=["numero_contrato", "codigo_municipio"])
    df_contratados = cleaning.limpar_tce(
        contratados_brutos,
        chave_duplicata=["numero_contrato", "codigo_municipio", "numero_documento_negociante"],
    )

    fornecedores_df = None
    if enriquecer_fornecedores:
        serie_cnpj = (
            df_contratados["numero_documento_negociante"]
            if "numero_documento_negociante" in df_contratados.columns
            else None
        )
        cnpjs = merge.extrair_cnpjs_distintos(serie_cnpj)
        fornecedores_df = _coletar_fornecedores(cnpjs, throttle_fornecedores)

    base = merge.montar_base_tce(df_contratos, df_contratados, fornecedores_df=fornecedores_df)
    metadados = {
        "fonte": "TCE-CE",
        "parametros": {
            "data_inicial": data_inicial,
            "data_final": data_final,
            "codigo_municipio": codigo_municipio,
            "enriquecer_fornecedores": enriquecer_fornecedores,
        },
        "totais_brutos": {
            "contratos": len(contratos_brutos),
            "contratados": len(contratados_brutos),
        },
    }
    return base, metadados


def consultar_tce_contratos(*, limite: int | None = 100, **kwargs: Any) -> dict[str, Any]:
    base, metadados = montar_base_tce_contratos(**kwargs)
    return {
        **metadados,
        "limite_resposta": limite,
        "totais": {"contratos": int(len(base))},
        "dados": dataframe_para_registros(base, limite=limite),
    }


def consultar_kpi_tce_me_por_mes(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    throttle_fornecedores: float = 0.3,
    limite: int | None = 100,
) -> dict[str, Any]:
    base, metadados = montar_base_tce_contratos(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        enriquecer_fornecedores=True,
        throttle_fornecedores=throttle_fornecedores,
    )
    resultado = kpis.calcular_participacao_me_por_mes(
        base,
        coluna_data="data_contrato",
        coluna_valor="valor_total_contrato",
    )
    return {
        **metadados,
        "limite_resposta": limite,
        "totais": {"contratos": int(len(base)), "periodos": int(len(resultado))},
        "kpi": "participacao_me_por_mes",
        "dados": dataframe_para_registros(resultado, limite=limite),
    }
