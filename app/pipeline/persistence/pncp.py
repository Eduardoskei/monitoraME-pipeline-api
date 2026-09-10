from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import database, orm
from app.core.models import (
    PncpContratacao,
    PncpContrato,
    PncpFornecedor,
    PncpItem,
    PncpPcaItem,
    PncpPcaPlano,
    PncpResultado,
)
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


def _json_payload(registro: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(registro, ensure_ascii=False, default=str))


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


def _decimal(registro: dict[str, Any], *colunas: str) -> Decimal | None:
    valor = _pick(registro, *colunas)
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _data(registro: dict[str, Any], *colunas: str) -> datetime | None:
    valor = _pick(registro, *colunas)
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)

    data = pd.to_datetime(valor, utc=True, errors="coerce")
    if pd.isna(data):
        return None
    return data.to_pydatetime()


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


def _executar_upsert(
    session: Session,
    model: type[Any],
    valores: dict[str, Any],
    chave_conflito: str,
    colunas_atualizacao: Iterable[str],
) -> bool:
    stmt = insert(model).values(**valores)
    update_values = {coluna: getattr(stmt.excluded, coluna) for coluna in colunas_atualizacao}
    update_values["atualizado_em"] = func.now()

    stmt = stmt.on_conflict_do_update(
        index_elements=[getattr(model.__table__.c, chave_conflito)],
        set_=update_values,
    ).returning(literal_column("(xmax = 0)").label("inserido"))

    return bool(session.execute(stmt).scalar_one())


def _contabilizar_upsert(
    session: Session,
    model: type[Any],
    valores: dict[str, Any],
    chave_conflito: str,
    colunas_atualizacao: Iterable[str],
) -> tuple[int, int]:
    inserido = _executar_upsert(session, model, valores, chave_conflito, colunas_atualizacao)
    return int(inserido), int(not inserido)


def _persistir_contratacoes(session: Session, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        numero_controle = _texto(registro, "numero_controle_pncp")
        if not numero_controle:
            continue

        valores = {
            "numero_controle_pncp": numero_controle,
            "cnpj_orgao": _cnpj(registro, "cnpj_orgao", "orgao_entidade_cnpj"),
            "ano_compra": _inteiro(registro, "ano_compra"),
            "sequencial_compra": _inteiro(registro, "sequencial_compra"),
            "modalidade_id": _inteiro(registro, "modalidade_id", "codigo_modalidade_contratacao"),
            "modalidade_nome": _texto(registro, "modalidade_nome"),
            "objeto_compra": _texto(registro, "objeto_compra"),
            "natureza_despesa_monitorada": _texto(registro, "natureza_despesa_monitorada"),
            "data_publicacao_pncp": _data(registro, "data_publicacao_pncp"),
            "data_atualizacao": _data(registro, "data_atualizacao"),
            "data_atualizacao_global": _data(registro, "data_atualizacao_global"),
            "payload": _json_payload(registro),
        }
        inserido, atualizado = _contabilizar_upsert(
            session,
            PncpContratacao,
            valores,
            "numero_controle_pncp",
            (
                "cnpj_orgao",
                "ano_compra",
                "sequencial_compra",
                "modalidade_id",
                "modalidade_nome",
                "objeto_compra",
                "natureza_despesa_monitorada",
                "data_publicacao_pncp",
                "data_atualizacao",
                "data_atualizacao_global",
                "payload",
            ),
        )
        inseridos += inserido
        atualizados += atualizado
    return inseridos, atualizados


def _persistir_itens(session: Session, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        numero_controle = _texto(registro, "numero_controle_pncp")
        if not numero_controle:
            continue

        numero_item = _inteiro(registro, "numero_item")
        item_id = _id_por_partes("pncp-item", (numero_controle, numero_item), registro)
        valores = {
            "item_id": item_id,
            "numero_controle_pncp": numero_controle,
            "numero_item": numero_item,
            "descricao": _texto(registro, "descricao"),
            "material_ou_servico_nome": _texto(registro, "material_ou_servico_nome", "nome_classificacao"),
            "item_categoria_id": _inteiro(registro, "item_categoria_id"),
            "item_categoria_nome": _texto(registro, "item_categoria_nome"),
            "categoria_item_catalogo_id": _inteiro(registro, "categoria_item_catalogo_id"),
            "categoria_item_catalogo_nome": _texto(registro, "categoria_item_catalogo_nome"),
            "classificacao_superior_codigo": _texto(registro, "classificacao_superior_codigo"),
            "classificacao_superior_nome": _texto(registro, "classificacao_superior_nome"),
            "ncm_nbs_codigo": _texto(registro, "ncm_nbs_codigo"),
            "ncm_nbs_descricao": _texto(registro, "ncm_nbs_descricao"),
            "valor_total": _decimal(registro, "valor_total"),
            "natureza_despesa_monitorada": _texto(registro, "natureza_despesa_monitorada"),
            "data_inclusao": _data(registro, "data_inclusao"),
            "data_atualizacao": _data(registro, "data_atualizacao"),
            "payload": _json_payload(registro),
        }
        inserido, atualizado = _contabilizar_upsert(
            session,
            PncpItem,
            valores,
            "item_id",
            (
                "numero_item",
                "descricao",
                "material_ou_servico_nome",
                "item_categoria_id",
                "item_categoria_nome",
                "categoria_item_catalogo_id",
                "categoria_item_catalogo_nome",
                "classificacao_superior_codigo",
                "classificacao_superior_nome",
                "ncm_nbs_codigo",
                "ncm_nbs_descricao",
                "valor_total",
                "natureza_despesa_monitorada",
                "data_inclusao",
                "data_atualizacao",
                "payload",
            ),
        )
        inseridos += inserido
        atualizados += atualizado
    return inseridos, atualizados


def _persistir_resultados(session: Session, df: pd.DataFrame | None) -> tuple[int, int]:
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
        valores = {
            "resultado_id": resultado_id,
            "numero_controle_pncp": numero_controle,
            "numero_item": numero_item,
            "ni_fornecedor": _texto(registro, "ni_fornecedor"),
            "nome_razao_social_fornecedor": _texto(registro, "nome_razao_social_fornecedor"),
            "porte_fornecedor_id": _inteiro(registro, "porte_fornecedor_id"),
            "porte_fornecedor_nome": _texto(registro, "porte_fornecedor_nome"),
            "porte_fornecedor_padronizado": _texto(registro, "porte_fornecedor_padronizado"),
            "valor_total_homologado": _decimal(registro, "valor_total_homologado"),
            "data_resultado": _data(registro, "data_resultado"),
            "data_cancelamento": _data(registro, "data_cancelamento"),
            "data_inclusao": _data(registro, "data_inclusao"),
            "data_atualizacao": _data(registro, "data_atualizacao"),
            "payload": _json_payload(registro),
        }
        inserido, atualizado = _contabilizar_upsert(
            session,
            PncpResultado,
            valores,
            "resultado_id",
            (
                "numero_item",
                "ni_fornecedor",
                "nome_razao_social_fornecedor",
                "porte_fornecedor_id",
                "porte_fornecedor_nome",
                "porte_fornecedor_padronizado",
                "valor_total_homologado",
                "data_resultado",
                "data_cancelamento",
                "data_inclusao",
                "data_atualizacao",
                "payload",
            ),
        )
        inseridos += inserido
        atualizados += atualizado
    return inseridos, atualizados


def _persistir_contratos(session: Session, df: pd.DataFrame | None) -> tuple[int, int]:
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
        valores = {
            "contrato_id": contrato_id,
            "numero_controle_pncp": numero_controle,
            "ano_contrato": _inteiro(registro, "ano_contrato"),
            "sequencial_contrato": _inteiro(registro, "sequencial_contrato"),
            "numero_contrato_empenho": _texto(registro, "numero_contrato_empenho"),
            "ni_fornecedor": _texto(registro, "ni_fornecedor"),
            "nome_razao_social_fornecedor": _texto(registro, "nome_razao_social_fornecedor"),
            "valor_global": _decimal(registro, "valor_global"),
            "data_assinatura": _data(registro, "data_assinatura"),
            "data_vigencia_inicio": _data(registro, "data_vigencia_inicio"),
            "data_vigencia_fim": _data(registro, "data_vigencia_fim"),
            "data_publicacao_pncp": _data(registro, "data_publicacao_pncp"),
            "data_atualizacao": _data(registro, "data_atualizacao"),
            "payload": _json_payload(registro),
        }
        inserido, atualizado = _contabilizar_upsert(
            session,
            PncpContrato,
            valores,
            "contrato_id",
            (
                "ano_contrato",
                "sequencial_contrato",
                "numero_contrato_empenho",
                "ni_fornecedor",
                "nome_razao_social_fornecedor",
                "valor_global",
                "data_assinatura",
                "data_vigencia_inicio",
                "data_vigencia_fim",
                "data_publicacao_pncp",
                "data_atualizacao",
                "payload",
            ),
        )
        inseridos += inserido
        atualizados += atualizado
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


def _persistir_pca_planos(session: Session, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        pca_id = _pca_id(registro)
        valores = {
            "pca_id": pca_id,
            "numero_controle_pncp": _texto(registro, "numero_controle_pncp"),
            "cnpj_orgao": _cnpj(registro, "cnpj_orgao", "cnpj"),
            "ano_pca": _inteiro(registro, "ano_pca"),
            "sequencial_pca": _inteiro(registro, "sequencial_pca"),
            "codigo_unidade": _texto(registro, "codigo_unidade"),
            "nome_unidade": _texto(registro, "nome_unidade"),
            "municipio": _texto(registro, "municipio", "municipio_nome"),
            "uf": _texto(registro, "uf", "uf_sigla"),
            "natureza_despesa_monitorada": _texto(registro, "natureza_despesa_monitorada"),
            "data_publicacao_pncp": _data(registro, "data_publicacao_pncp"),
            "data_atualizacao": _data(registro, "data_atualizacao"),
            "data_atualizacao_global": _data(registro, "data_atualizacao_global"),
            "payload": _json_payload(registro),
        }
        inserido, atualizado = _contabilizar_upsert(
            session,
            PncpPcaPlano,
            valores,
            "pca_id",
            (
                "numero_controle_pncp",
                "cnpj_orgao",
                "ano_pca",
                "sequencial_pca",
                "codigo_unidade",
                "nome_unidade",
                "municipio",
                "uf",
                "natureza_despesa_monitorada",
                "data_publicacao_pncp",
                "data_atualizacao",
                "data_atualizacao_global",
                "payload",
            ),
        )
        inseridos += inserido
        atualizados += atualizado
    return inseridos, atualizados


def _persistir_pca_itens(session: Session, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        pca_id = _pca_id(registro)
        numero_item = _inteiro(registro, "numero_item")
        pca_item_id = _id_por_partes("pncp-pca-item", (pca_id, numero_item), registro)
        valores = {
            "pca_item_id": pca_item_id,
            "pca_id": pca_id,
            "numero_controle_pncp": _texto(registro, "numero_controle_pncp"),
            "cnpj_orgao": _cnpj(registro, "cnpj_orgao", "cnpj"),
            "ano_pca": _inteiro(registro, "ano_pca"),
            "sequencial_pca": _inteiro(registro, "sequencial_pca"),
            "numero_item": numero_item,
            "categoria_item_pca_id": _inteiro(registro, "categoria_item_pca_id", "categoria_item_pcaid"),
            "categoria_item_pca_nome": _texto(registro, "categoria_item_pca_nome"),
            "classificacao_superior_codigo": _texto(registro, "classificacao_superior_codigo"),
            "classificacao_superior_nome": _texto(registro, "classificacao_superior_nome"),
            "pdm_codigo": _texto(registro, "pdm_codigo"),
            "pdm_descricao": _texto(registro, "pdm_descricao"),
            "codigo_item": _texto(registro, "codigo_item"),
            "descricao": _texto(registro, "descricao", "descricao_item"),
            "unidade_fornecimento": _texto(registro, "unidade_fornecimento"),
            "quantidade": _decimal(registro, "quantidade"),
            "valor_unitario": _decimal(registro, "valor_unitario"),
            "valor_total": _decimal(registro, "valor_total"),
            "valor_orcamento_exercicio": _decimal(registro, "valor_orcamento_exercicio"),
            "natureza_despesa_monitorada": _texto(registro, "natureza_despesa_monitorada"),
            "data_desejada": _data(registro, "data_desejada"),
            "data_publicacao_pncp": _data(registro, "data_publicacao_pncp"),
            "data_inclusao": _data(registro, "data_inclusao"),
            "data_atualizacao": _data(registro, "data_atualizacao"),
            "payload": _json_payload(registro),
        }
        inserido, atualizado = _contabilizar_upsert(
            session,
            PncpPcaItem,
            valores,
            "pca_item_id",
            (
                "pca_id",
                "numero_controle_pncp",
                "cnpj_orgao",
                "ano_pca",
                "sequencial_pca",
                "numero_item",
                "categoria_item_pca_id",
                "categoria_item_pca_nome",
                "classificacao_superior_codigo",
                "classificacao_superior_nome",
                "pdm_codigo",
                "pdm_descricao",
                "codigo_item",
                "descricao",
                "unidade_fornecimento",
                "quantidade",
                "valor_unitario",
                "valor_total",
                "valor_orcamento_exercicio",
                "natureza_despesa_monitorada",
                "data_desejada",
                "data_publicacao_pncp",
                "data_inclusao",
                "data_atualizacao",
                "payload",
            ),
        )
        inseridos += inserido
        atualizados += atualizado
    return inseridos, atualizados


def _persistir_fornecedores(session: Session, df: pd.DataFrame | None) -> tuple[int, int]:
    inseridos = 0
    atualizados = 0
    for registro in _registros(df):
        cnpj = _cnpj(registro, "cnpj")
        if not cnpj:
            continue

        valores = {
            "cnpj": cnpj,
            "razao_social": _texto(registro, "razao_social"),
            "porte": _texto(registro, "porte"),
            "porte_padronizado": _texto(registro, "porte_padronizado"),
            "municipio_sede": _texto(registro, "municipio_sede"),
            "uf_sede": _texto(registro, "uf_sede"),
            "cnae_principal_codigo": _texto(registro, "cnae_principal_codigo"),
            "cnae_principal_descricao": _texto(registro, "cnae_principal_descricao"),
            "elegivel_me": _booleano(registro, "elegivel_me"),
            "cnpj_valido": _booleano(registro, "cnpj_valido"),
            "opencnpj_status": _texto(registro, "opencnpj_status"),
            "optante_simples_nacional": _booleano(registro, "optante_simples_nacional"),
            "optante_mei": _booleano(registro, "optante_mei"),
            "payload": _json_payload(registro),
        }
        inserido, atualizado = _contabilizar_upsert(
            session,
            PncpFornecedor,
            valores,
            "cnpj",
            (
                "razao_social",
                "porte",
                "porte_padronizado",
                "municipio_sede",
                "uf_sede",
                "cnae_principal_codigo",
                "cnae_principal_descricao",
                "elegivel_me",
                "cnpj_valido",
                "opencnpj_status",
                "optante_simples_nacional",
                "optante_mei",
                "payload",
            ),
        )
        inseridos += inserido
        atualizados += atualizado
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

    with orm.main_session() as session:
        for tabela, persistir in ordem:
            inseridos, atualizados = persistir(session, tabelas.get(tabela))
            resultado.adicionar(tabela, inseridos, atualizados)

    return resultado


__all__ = ["ResultadoPersistencia", "persistir_tabelas"]
