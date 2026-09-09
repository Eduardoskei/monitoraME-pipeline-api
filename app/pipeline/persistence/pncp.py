from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Iterable

import pandas as pd

from app.core import database
from app.utils import somente_digitos


@dataclass
class ResultadoPersistencia:
    inseridos: int = 0
    atualizados: int = 0
    por_tabela: dict[str, dict[str, int]] = field(default_factory=dict)

    def adicionar(self, tabela: str, inseridos: int, atualizados: int) -> None:
        self.inseridos += inseridos
        self.atualizados += atualizados
        self.por_tabela[tabela] = {
            "inseridos": inseridos,
            "atualizados": atualizados,
        }


def _valor_json(valor: Any) -> Any:
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
        return {str(chave): _valor_json(item) for chave, item in valor.items()}

    if isinstance(valor, (list, tuple)):
        return [_valor_json(item) for item in valor]

    if hasattr(valor, "item") and not isinstance(valor, (str, bytes, bytearray)):
        try:
            return valor.item()
        except (AttributeError, TypeError, ValueError):
            return valor

    return valor


def _registros(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    return [
        {str(chave): _valor_json(valor) for chave, valor in registro.items()}
        for registro in df.to_dict(orient="records")
    ]


def _jsonb(registro: dict[str, Any]) -> str:
    return json.dumps(registro, ensure_ascii=False, default=str)


def _pick(registro: dict[str, Any], *colunas: str) -> Any:
    for coluna in colunas:
        valor = registro.get(coluna)
        if valor is None:
            continue
        if isinstance(valor, str) and valor.strip().lower() in {"", "nao_informado"}:
            continue
        if valor != "":
            return valor
    return None


def _texto(registro: dict[str, Any], *colunas: str) -> str | None:
    valor = _pick(registro, *colunas)
    if valor in (None, ""):
        return None
    texto = str(valor).strip()
    return texto or None


def _inteiro(registro: dict[str, Any], *colunas: str) -> int | None:
    valor = _pick(registro, *colunas)
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _booleano(registro: dict[str, Any], *colunas: str) -> bool | None:
    valor = _pick(registro, *colunas)
    if isinstance(valor, bool):
        return valor
    return None


def _cnpj(registro: dict[str, Any], *colunas: str) -> str | None:
    for coluna in colunas:
        cnpj = somente_digitos(registro.get(coluna))
        if len(cnpj) == 14:
            return cnpj
    return None


def _hash_id(prefixo: str, registro: dict[str, Any]) -> str:
    payload = json.dumps(registro, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefixo}:{sha256(payload.encode('utf-8')).hexdigest()}"


def _id_por_partes(prefixo: str, partes: Iterable[Any], registro: dict[str, Any]) -> str:
    valores = [str(parte).strip() for parte in partes if parte not in (None, "")]
    if valores:
        return f"{prefixo}:{':'.join(valores)}"
    return _hash_id(prefixo, registro)


def _resultado_upsert(cur: Any) -> bool:
    row = cur.fetchone()
    return bool(row and row[0])


def _executar(cur: Any, sql: str, params: tuple[Any, ...]) -> bool:
    cur.execute(sql, params)
    return _resultado_upsert(cur)


_UPSERT_CONTRATACAO_SQL = """
    INSERT INTO pncp_contratacoes (
        numero_controle_pncp,
        cnpj_orgao,
        ano_compra,
        sequencial_compra,
        modalidade_id,
        modalidade_nome,
        objeto_compra,
        natureza_despesa_monitorada,
        data_publicacao_pncp,
        data_atualizacao,
        data_atualizacao_global,
        payload,
        atualizado_em
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
    ON CONFLICT (numero_controle_pncp)
    DO UPDATE SET
        cnpj_orgao = EXCLUDED.cnpj_orgao,
        ano_compra = EXCLUDED.ano_compra,
        sequencial_compra = EXCLUDED.sequencial_compra,
        modalidade_id = EXCLUDED.modalidade_id,
        modalidade_nome = EXCLUDED.modalidade_nome,
        objeto_compra = EXCLUDED.objeto_compra,
        natureza_despesa_monitorada = EXCLUDED.natureza_despesa_monitorada,
        data_publicacao_pncp = EXCLUDED.data_publicacao_pncp,
        data_atualizacao = EXCLUDED.data_atualizacao,
        data_atualizacao_global = EXCLUDED.data_atualizacao_global,
        payload = EXCLUDED.payload,
        atualizado_em = NOW()
    RETURNING (xmax = 0);
"""

_UPSERT_ITEM_SQL = """
    INSERT INTO pncp_itens (
        item_id,
        numero_controle_pncp,
        numero_item,
        descricao,
        material_ou_servico_nome,
        item_categoria_id,
        item_categoria_nome,
        categoria_item_catalogo_id,
        categoria_item_catalogo_nome,
        classificacao_superior_codigo,
        classificacao_superior_nome,
        ncm_nbs_codigo,
        ncm_nbs_descricao,
        valor_total,
        natureza_despesa_monitorada,
        data_inclusao,
        data_atualizacao,
        payload,
        atualizado_em
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
    ON CONFLICT (item_id)
    DO UPDATE SET
        numero_item = EXCLUDED.numero_item,
        descricao = EXCLUDED.descricao,
        material_ou_servico_nome = EXCLUDED.material_ou_servico_nome,
        item_categoria_id = EXCLUDED.item_categoria_id,
        item_categoria_nome = EXCLUDED.item_categoria_nome,
        categoria_item_catalogo_id = EXCLUDED.categoria_item_catalogo_id,
        categoria_item_catalogo_nome = EXCLUDED.categoria_item_catalogo_nome,
        classificacao_superior_codigo = EXCLUDED.classificacao_superior_codigo,
        classificacao_superior_nome = EXCLUDED.classificacao_superior_nome,
        ncm_nbs_codigo = EXCLUDED.ncm_nbs_codigo,
        ncm_nbs_descricao = EXCLUDED.ncm_nbs_descricao,
        valor_total = EXCLUDED.valor_total,
        natureza_despesa_monitorada = EXCLUDED.natureza_despesa_monitorada,
        data_inclusao = EXCLUDED.data_inclusao,
        data_atualizacao = EXCLUDED.data_atualizacao,
        payload = EXCLUDED.payload,
        atualizado_em = NOW()
    RETURNING (xmax = 0);
"""

_UPSERT_RESULTADO_SQL = """
    INSERT INTO pncp_resultados (
        resultado_id,
        numero_controle_pncp,
        numero_item,
        ni_fornecedor,
        nome_razao_social_fornecedor,
        porte_fornecedor_id,
        porte_fornecedor_nome,
        porte_fornecedor_padronizado,
        valor_total_homologado,
        data_resultado,
        data_cancelamento,
        data_inclusao,
        data_atualizacao,
        payload,
        atualizado_em
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
    ON CONFLICT (resultado_id)
    DO UPDATE SET
        numero_item = EXCLUDED.numero_item,
        ni_fornecedor = EXCLUDED.ni_fornecedor,
        nome_razao_social_fornecedor = EXCLUDED.nome_razao_social_fornecedor,
        porte_fornecedor_id = EXCLUDED.porte_fornecedor_id,
        porte_fornecedor_nome = EXCLUDED.porte_fornecedor_nome,
        porte_fornecedor_padronizado = EXCLUDED.porte_fornecedor_padronizado,
        valor_total_homologado = EXCLUDED.valor_total_homologado,
        data_resultado = EXCLUDED.data_resultado,
        data_cancelamento = EXCLUDED.data_cancelamento,
        data_inclusao = EXCLUDED.data_inclusao,
        data_atualizacao = EXCLUDED.data_atualizacao,
        payload = EXCLUDED.payload,
        atualizado_em = NOW()
    RETURNING (xmax = 0);
"""

_UPSERT_CONTRATO_SQL = """
    INSERT INTO pncp_contratos (
        contrato_id,
        numero_controle_pncp,
        ano_contrato,
        sequencial_contrato,
        numero_contrato_empenho,
        ni_fornecedor,
        nome_razao_social_fornecedor,
        valor_global,
        data_assinatura,
        data_vigencia_inicio,
        data_vigencia_fim,
        data_publicacao_pncp,
        data_atualizacao,
        payload,
        atualizado_em
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
    ON CONFLICT (contrato_id)
    DO UPDATE SET
        ano_contrato = EXCLUDED.ano_contrato,
        sequencial_contrato = EXCLUDED.sequencial_contrato,
        numero_contrato_empenho = EXCLUDED.numero_contrato_empenho,
        ni_fornecedor = EXCLUDED.ni_fornecedor,
        nome_razao_social_fornecedor = EXCLUDED.nome_razao_social_fornecedor,
        valor_global = EXCLUDED.valor_global,
        data_assinatura = EXCLUDED.data_assinatura,
        data_vigencia_inicio = EXCLUDED.data_vigencia_inicio,
        data_vigencia_fim = EXCLUDED.data_vigencia_fim,
        data_publicacao_pncp = EXCLUDED.data_publicacao_pncp,
        data_atualizacao = EXCLUDED.data_atualizacao,
        payload = EXCLUDED.payload,
        atualizado_em = NOW()
    RETURNING (xmax = 0);
"""

_UPSERT_PCA_PLANO_SQL = """
    INSERT INTO pncp_pca_planos (
        pca_id,
        numero_controle_pncp,
        cnpj_orgao,
        ano_pca,
        sequencial_pca,
        codigo_unidade,
        nome_unidade,
        municipio,
        uf,
        natureza_despesa_monitorada,
        data_publicacao_pncp,
        data_atualizacao,
        data_atualizacao_global,
        payload,
        atualizado_em
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
    ON CONFLICT (pca_id)
    DO UPDATE SET
        numero_controle_pncp = EXCLUDED.numero_controle_pncp,
        cnpj_orgao = EXCLUDED.cnpj_orgao,
        ano_pca = EXCLUDED.ano_pca,
        sequencial_pca = EXCLUDED.sequencial_pca,
        codigo_unidade = EXCLUDED.codigo_unidade,
        nome_unidade = EXCLUDED.nome_unidade,
        municipio = EXCLUDED.municipio,
        uf = EXCLUDED.uf,
        natureza_despesa_monitorada = EXCLUDED.natureza_despesa_monitorada,
        data_publicacao_pncp = EXCLUDED.data_publicacao_pncp,
        data_atualizacao = EXCLUDED.data_atualizacao,
        data_atualizacao_global = EXCLUDED.data_atualizacao_global,
        payload = EXCLUDED.payload,
        atualizado_em = NOW()
    RETURNING (xmax = 0);
"""

_UPSERT_PCA_ITEM_SQL = """
    INSERT INTO pncp_pca_itens (
        pca_item_id,
        pca_id,
        numero_controle_pncp,
        cnpj_orgao,
        ano_pca,
        sequencial_pca,
        numero_item,
        categoria_item_pca_id,
        categoria_item_pca_nome,
        classificacao_superior_codigo,
        classificacao_superior_nome,
        pdm_codigo,
        pdm_descricao,
        codigo_item,
        descricao,
        unidade_fornecimento,
        quantidade,
        valor_unitario,
        valor_total,
        valor_orcamento_exercicio,
        natureza_despesa_monitorada,
        data_desejada,
        data_publicacao_pncp,
        data_inclusao,
        data_atualizacao,
        payload,
        atualizado_em
    )
    VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s::jsonb, NOW()
    )
    ON CONFLICT (pca_item_id)
    DO UPDATE SET
        pca_id = EXCLUDED.pca_id,
        numero_controle_pncp = EXCLUDED.numero_controle_pncp,
        cnpj_orgao = EXCLUDED.cnpj_orgao,
        ano_pca = EXCLUDED.ano_pca,
        sequencial_pca = EXCLUDED.sequencial_pca,
        numero_item = EXCLUDED.numero_item,
        categoria_item_pca_id = EXCLUDED.categoria_item_pca_id,
        categoria_item_pca_nome = EXCLUDED.categoria_item_pca_nome,
        classificacao_superior_codigo = EXCLUDED.classificacao_superior_codigo,
        classificacao_superior_nome = EXCLUDED.classificacao_superior_nome,
        pdm_codigo = EXCLUDED.pdm_codigo,
        pdm_descricao = EXCLUDED.pdm_descricao,
        codigo_item = EXCLUDED.codigo_item,
        descricao = EXCLUDED.descricao,
        unidade_fornecimento = EXCLUDED.unidade_fornecimento,
        quantidade = EXCLUDED.quantidade,
        valor_unitario = EXCLUDED.valor_unitario,
        valor_total = EXCLUDED.valor_total,
        valor_orcamento_exercicio = EXCLUDED.valor_orcamento_exercicio,
        natureza_despesa_monitorada = EXCLUDED.natureza_despesa_monitorada,
        data_desejada = EXCLUDED.data_desejada,
        data_publicacao_pncp = EXCLUDED.data_publicacao_pncp,
        data_inclusao = EXCLUDED.data_inclusao,
        data_atualizacao = EXCLUDED.data_atualizacao,
        payload = EXCLUDED.payload,
        atualizado_em = NOW()
    RETURNING (xmax = 0);
"""

_UPSERT_FORNECEDOR_SQL = """
    INSERT INTO pncp_fornecedores (
        cnpj,
        razao_social,
        porte,
        porte_padronizado,
        municipio_sede,
        uf_sede,
        cnae_principal_codigo,
        cnae_principal_descricao,
        elegivel_me,
        cnpj_valido,
        opencnpj_status,
        optante_simples_nacional,
        optante_mei,
        payload,
        atualizado_em
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
    ON CONFLICT (cnpj)
    DO UPDATE SET
        razao_social = EXCLUDED.razao_social,
        porte = EXCLUDED.porte,
        porte_padronizado = EXCLUDED.porte_padronizado,
        municipio_sede = EXCLUDED.municipio_sede,
        uf_sede = EXCLUDED.uf_sede,
        cnae_principal_codigo = EXCLUDED.cnae_principal_codigo,
        cnae_principal_descricao = EXCLUDED.cnae_principal_descricao,
        elegivel_me = EXCLUDED.elegivel_me,
        cnpj_valido = EXCLUDED.cnpj_valido,
        opencnpj_status = EXCLUDED.opencnpj_status,
        optante_simples_nacional = EXCLUDED.optante_simples_nacional,
        optante_mei = EXCLUDED.optante_mei,
        payload = EXCLUDED.payload,
        atualizado_em = NOW()
    RETURNING (xmax = 0);
"""


def _persistir_contratacoes(cur: Any, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        numero_controle = _texto(registro, "numero_controle_pncp")
        if not numero_controle:
            continue

        inserido = _executar(
            cur,
            _UPSERT_CONTRATACAO_SQL,
            (
                numero_controle,
                _cnpj(registro, "cnpj_orgao", "orgao_entidade_cnpj"),
                _inteiro(registro, "ano_compra"),
                _inteiro(registro, "sequencial_compra"),
                _inteiro(registro, "modalidade_id", "codigo_modalidade_contratacao"),
                _texto(registro, "modalidade_nome"),
                _texto(registro, "objeto_compra"),
                _texto(registro, "natureza_despesa_monitorada"),
                _texto(registro, "data_publicacao_pncp"),
                _texto(registro, "data_atualizacao"),
                _texto(registro, "data_atualizacao_global"),
                _jsonb(registro),
            ),
        )
        inseridos += int(inserido)
        atualizados += int(not inserido)
    return inseridos, atualizados


def _persistir_itens(cur: Any, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        numero_controle = _texto(registro, "numero_controle_pncp")
        if not numero_controle:
            continue

        numero_item = _inteiro(registro, "numero_item")
        item_id = _id_por_partes("pncp-item", (numero_controle, numero_item), registro)
        inserido = _executar(
            cur,
            _UPSERT_ITEM_SQL,
            (
                item_id,
                numero_controle,
                numero_item,
                _texto(registro, "descricao"),
                _texto(registro, "material_ou_servico_nome", "nome_classificacao"),
                _inteiro(registro, "item_categoria_id"),
                _texto(registro, "item_categoria_nome"),
                _inteiro(registro, "categoria_item_catalogo_id"),
                _texto(registro, "categoria_item_catalogo_nome"),
                _texto(registro, "classificacao_superior_codigo"),
                _texto(registro, "classificacao_superior_nome"),
                _texto(registro, "ncm_nbs_codigo"),
                _texto(registro, "ncm_nbs_descricao"),
                _pick(registro, "valor_total"),
                _texto(registro, "natureza_despesa_monitorada"),
                _texto(registro, "data_inclusao"),
                _texto(registro, "data_atualizacao"),
                _jsonb(registro),
            ),
        )
        inseridos += int(inserido)
        atualizados += int(not inserido)
    return inseridos, atualizados


def _persistir_resultados(cur: Any, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        numero_controle = _texto(registro, "numero_controle_pncp")
        if not numero_controle:
            continue

        numero_item = _inteiro(registro, "numero_item")
        sequencial = _pick(registro, "sequencial_resultado", "sequencial_resultado_item")
        resultado_id = _id_por_partes(
            "pncp-resultado",
            (
                numero_controle,
                numero_item,
                sequencial,
                _texto(registro, "ni_fornecedor"),
            ),
            registro,
        )
        inserido = _executar(
            cur,
            _UPSERT_RESULTADO_SQL,
            (
                resultado_id,
                numero_controle,
                numero_item,
                _texto(registro, "ni_fornecedor"),
                _texto(registro, "nome_razao_social_fornecedor"),
                _inteiro(registro, "porte_fornecedor_id"),
                _texto(registro, "porte_fornecedor_nome"),
                _texto(registro, "porte_fornecedor_padronizado"),
                _pick(registro, "valor_total_homologado"),
                _texto(registro, "data_resultado"),
                _texto(registro, "data_cancelamento"),
                _texto(registro, "data_inclusao"),
                _texto(registro, "data_atualizacao"),
                _jsonb(registro),
            ),
        )
        inseridos += int(inserido)
        atualizados += int(not inserido)
    return inseridos, atualizados


def _persistir_contratos(cur: Any, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        numero_controle = _texto(registro, "numero_controle_pncp")
        if not numero_controle:
            continue

        contrato_id = _id_por_partes(
            "pncp-contrato",
            (
                numero_controle,
                _inteiro(registro, "ano_contrato"),
                _inteiro(registro, "sequencial_contrato"),
                _texto(registro, "numero_contrato_empenho"),
            ),
            registro,
        )
        inserido = _executar(
            cur,
            _UPSERT_CONTRATO_SQL,
            (
                contrato_id,
                numero_controle,
                _inteiro(registro, "ano_contrato"),
                _inteiro(registro, "sequencial_contrato"),
                _texto(registro, "numero_contrato_empenho"),
                _texto(registro, "ni_fornecedor"),
                _texto(registro, "nome_razao_social_fornecedor"),
                _pick(registro, "valor_global"),
                _texto(registro, "data_assinatura"),
                _texto(registro, "data_vigencia_inicio"),
                _texto(registro, "data_vigencia_fim"),
                _texto(registro, "data_publicacao_pncp"),
                _texto(registro, "data_atualizacao"),
                _jsonb(registro),
            ),
        )
        inseridos += int(inserido)
        atualizados += int(not inserido)
    return inseridos, atualizados


def _pca_id(registro: dict[str, Any]) -> str:
    return _texto(registro, "pca_id", "numero_controle_pncp") or _id_por_partes(
        "pncp-pca",
        (
            _cnpj(registro, "cnpj_orgao", "cnpj"),
            _inteiro(registro, "ano_pca"),
            _inteiro(registro, "sequencial_pca"),
        ),
        registro,
    )


def _persistir_pca_planos(cur: Any, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        pca_id = _pca_id(registro)
        inserido = _executar(
            cur,
            _UPSERT_PCA_PLANO_SQL,
            (
                pca_id,
                _texto(registro, "numero_controle_pncp"),
                _cnpj(registro, "cnpj_orgao", "cnpj"),
                _inteiro(registro, "ano_pca"),
                _inteiro(registro, "sequencial_pca"),
                _texto(registro, "codigo_unidade"),
                _texto(registro, "nome_unidade"),
                _texto(registro, "municipio", "municipio_nome"),
                _texto(registro, "uf", "uf_sigla"),
                _texto(registro, "natureza_despesa_monitorada"),
                _texto(registro, "data_publicacao_pncp"),
                _texto(registro, "data_atualizacao"),
                _texto(registro, "data_atualizacao_global"),
                _jsonb(registro),
            ),
        )
        inseridos += int(inserido)
        atualizados += int(not inserido)
    return inseridos, atualizados


def _persistir_pca_itens(cur: Any, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        pca_id = _pca_id(registro)
        numero_item = _inteiro(registro, "numero_item")
        pca_item_id = _id_por_partes("pncp-pca-item", (pca_id, numero_item), registro)
        inserido = _executar(
            cur,
            _UPSERT_PCA_ITEM_SQL,
            (
                pca_item_id,
                pca_id,
                _texto(registro, "numero_controle_pncp"),
                _cnpj(registro, "cnpj_orgao", "cnpj"),
                _inteiro(registro, "ano_pca"),
                _inteiro(registro, "sequencial_pca"),
                numero_item,
                _inteiro(registro, "categoria_item_pca_id", "categoria_item_pcaid"),
                _texto(registro, "categoria_item_pca_nome"),
                _texto(registro, "classificacao_superior_codigo"),
                _texto(registro, "classificacao_superior_nome"),
                _texto(registro, "pdm_codigo"),
                _texto(registro, "pdm_descricao"),
                _texto(registro, "codigo_item"),
                _texto(registro, "descricao", "descricao_item"),
                _texto(registro, "unidade_fornecimento"),
                _pick(registro, "quantidade"),
                _pick(registro, "valor_unitario"),
                _pick(registro, "valor_total"),
                _pick(registro, "valor_orcamento_exercicio"),
                _texto(registro, "natureza_despesa_monitorada"),
                _texto(registro, "data_desejada"),
                _texto(registro, "data_publicacao_pncp"),
                _texto(registro, "data_inclusao"),
                _texto(registro, "data_atualizacao"),
                _jsonb(registro),
            ),
        )
        inseridos += int(inserido)
        atualizados += int(not inserido)
    return inseridos, atualizados


def _persistir_fornecedores(cur: Any, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        cnpj = _cnpj(registro, "cnpj")
        if not cnpj:
            continue

        inserido = _executar(
            cur,
            _UPSERT_FORNECEDOR_SQL,
            (
                cnpj,
                _texto(registro, "razao_social"),
                _texto(registro, "porte"),
                _texto(registro, "porte_padronizado"),
                _texto(registro, "municipio_sede"),
                _texto(registro, "uf_sede"),
                _texto(registro, "cnae_principal_codigo"),
                _texto(registro, "cnae_principal_descricao"),
                _booleano(registro, "elegivel_me"),
                _booleano(registro, "cnpj_valido"),
                _texto(registro, "opencnpj_status"),
                _booleano(registro, "optante_simples_nacional"),
                _booleano(registro, "optante_mei"),
                _jsonb(registro),
            ),
        )
        inseridos += int(inserido)
        atualizados += int(not inserido)
    return inseridos, atualizados


def persistir_tabelas(tabelas: dict[str, pd.DataFrame]) -> ResultadoPersistencia:
    database.init_db()
    resultado = ResultadoPersistencia()
    ordem = (
        ("contratacoes", _persistir_contratacoes),
        ("itens", _persistir_itens),
        ("resultados", _persistir_resultados),
        ("contratos", _persistir_contratos),
        ("pca_planos", _persistir_pca_planos),
        ("pca_itens", _persistir_pca_itens),
        ("fornecedores", _persistir_fornecedores),
    )

    conn = database.get_conn()
    try:
        with conn, conn.cursor() as cur:
            for tabela, persistir in ordem:
                inseridos, atualizados = persistir(cur, tabelas.get(tabela))
                resultado.adicionar(tabela, inseridos, atualizados)
    finally:
        database.put_conn(conn)

    return resultado


__all__ = ["ResultadoPersistencia", "persistir_tabelas"]
