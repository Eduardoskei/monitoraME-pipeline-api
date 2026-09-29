"""Contrato canônico de empenhos e anulações provenientes do SIM/TCE-CE."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.pipeline.naturezas_despesa import (
    codigo_natureza_despesa_monitorado,
    normalizar_codigo_natureza_despesa,
)
from app.utils import (
    normalizar_cnpj,
    normalizar_cpf,
    somente_digitos,
    validar_cnpj,
    validar_cpf,
)


FONTE_TCE_CE = "TCE-CE"
TIPO_REGISTRO_EMPENHO = "EMPENHO"
TIPO_REGISTRO_ANULACAO = "ANULACAO_EMPENHO"
CENTAVOS = Decimal("0.01")


class ContratoDespesaTceInvalido(ValueError):
    """Um registro do TCE não satisfaz o contrato mínimo do domínio."""


def _texto(valor: Any, campo: str, *, obrigatorio: bool = True) -> str | None:
    if valor is None:
        texto = ""
    else:
        texto = str(valor).strip()
    if obrigatorio and not texto:
        raise ContratoDespesaTceInvalido(f"Campo obrigatório ausente no TCE-CE: {campo}.")
    return texto or None


def _inteiro(valor: Any, campo: str) -> int:
    if isinstance(valor, bool):
        raise ContratoDespesaTceInvalido(f"Campo inválido no TCE-CE: {campo}.")
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError) as error:
        raise ContratoDespesaTceInvalido(f"Campo inválido no TCE-CE: {campo}.") from error


def _exercicio(valor: Any) -> tuple[int, int]:
    exercicio_orcamento = _inteiro(valor, "exercicio_orcamento")
    if exercicio_orcamento < 10000 or exercicio_orcamento % 100 != 0:
        raise ContratoDespesaTceInvalido(
            "exercicio_orcamento deve usar o formato AAAA00 do SIM/TCE-CE."
        )
    return exercicio_orcamento, exercicio_orcamento // 100


def _competencia(valor: Any) -> str:
    referencia = _inteiro(valor, "data_referencia_doc")
    ano, mes = divmod(referencia, 100)
    if ano < 1 or mes < 1 or mes > 12:
        raise ContratoDespesaTceInvalido(
            "data_referencia_doc deve usar o formato AAAAMM do SIM/TCE-CE."
        )
    return f"{ano:04d}-{mes:02d}"


def _data_civil(valor: Any, campo: str) -> str:
    texto = _texto(valor, campo)
    assert texto is not None
    candidato = texto[:10] if len(texto) >= 10 and texto[4:5] == "-" else texto
    if len(candidato) == 8 and candidato.isdigit():
        candidato = f"{candidato[:4]}-{candidato[4:6]}-{candidato[6:]}"
    try:
        return date.fromisoformat(candidato).isoformat()
    except ValueError as error:
        raise ContratoDespesaTceInvalido(f"Data inválida no TCE-CE: {campo}={texto!r}.") from error


def valor_para_centavos(valor: Any, campo: str) -> int:
    """Converte decimal textual/nativo uma única vez, com arredondamento half-up."""
    if valor is None or isinstance(valor, bool):
        raise ContratoDespesaTceInvalido(f"Campo monetário inválido no TCE-CE: {campo}.")
    texto = str(valor).strip().replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        decimal = Decimal(texto)
    except (InvalidOperation, ValueError) as error:
        raise ContratoDespesaTceInvalido(
            f"Campo monetário inválido no TCE-CE: {campo}={valor!r}."
        ) from error
    if not decimal.is_finite():
        raise ContratoDespesaTceInvalido(f"Campo monetário inválido no TCE-CE: {campo}.")
    return int(decimal.quantize(CENTAVOS, rounding=ROUND_HALF_UP) * 100)


def _chave_empenho(
    codigo_municipio: str,
    exercicio_orcamento: int,
    codigo_orgao: str,
    codigo_unidade: str,
    data_empenho: str,
    numero_empenho: str,
) -> str:
    return "|".join(
        (
            codigo_municipio,
            str(exercicio_orcamento),
            codigo_orgao,
            codigo_unidade,
            data_empenho,
            numero_empenho,
        )
    )


def _campos_identificacao(registro: dict[str, Any], *, campo_numero: str) -> dict[str, Any]:
    codigo_municipio = _texto(registro.get("codigo_municipio"), "codigo_municipio")
    codigo_orgao = _texto(registro.get("codigo_orgao"), "codigo_orgao")
    codigo_unidade = _texto(
        registro.get("codigo_unidade_orcamentaria"),
        "codigo_unidade_orcamentaria",
    )
    numero_empenho = _texto(registro.get(campo_numero), campo_numero)
    assert codigo_municipio and codigo_orgao and codigo_unidade and numero_empenho

    exercicio_orcamento, exercicio = _exercicio(registro.get("exercicio_orcamento"))
    competencia = _competencia(registro.get("data_referencia_doc"))
    if int(competencia[:4]) != exercicio:
        raise ContratoDespesaTceInvalido(
            "data_referencia_doc não pertence ao exercicio_orcamento informado."
        )
    data_empenho = _data_civil(registro.get("data_emissao_empenho"), "data_emissao_empenho")

    return {
        "fonte": FONTE_TCE_CE,
        "codigo_municipio_tce": codigo_municipio,
        "exercicio_orcamento": exercicio_orcamento,
        "exercicio": exercicio,
        "competencia": competencia,
        "codigo_orgao": codigo_orgao,
        "codigo_unidade_orcamentaria": codigo_unidade,
        "data_empenho": data_empenho,
        "numero_empenho": numero_empenho,
        "chave_empenho": _chave_empenho(
            codigo_municipio,
            exercicio_orcamento,
            codigo_orgao,
            codigo_unidade,
            data_empenho,
            numero_empenho,
        ),
    }


def _documentos_fornecedor(registro: dict[str, Any]) -> dict[str, Any]:
    tipo = _texto(registro.get("codigo_tipo_negociante"), "codigo_tipo_negociante")
    documento = somente_digitos(registro.get("numero_documento_negociante")) or None
    cnpj = normalizar_cnpj(documento) if tipo == "1" else None
    cpf = normalizar_cpf(documento) if tipo == "2" else None
    return {
        "tipo_documento_fornecedor": tipo,
        "documento_fornecedor": documento,
        "cnpj_fornecedor": cnpj if validar_cnpj(cnpj) else None,
        "cpf_fornecedor": cpf if validar_cpf(cpf) else None,
    }


def mapear_nota_empenho(registro: dict[str, Any]) -> dict[str, Any]:
    """Mapeia uma nota de empenho bruta para o contrato canônico da ingestão."""
    base = _campos_identificacao(registro, campo_numero="numero_empenho")
    natureza_codigo = _texto(registro.get("codigo_elemento_despesa"), "codigo_elemento_despesa")
    assert natureza_codigo is not None
    if len(natureza_codigo) != 8 or not natureza_codigo.isdigit():
        raise ContratoDespesaTceInvalido(
            "codigo_elemento_despesa deve conter os oito dígitos publicados pelo TCE-CE."
        )
    elemento = normalizar_codigo_natureza_despesa(natureza_codigo)
    uf_informada = _texto(registro.get("codigo_uf"), "codigo_uf", obrigatorio=False)

    return {
        **base,
        "tipo_registro": TIPO_REGISTRO_EMPENHO,
        "codigo_natureza_despesa": natureza_codigo,
        "codigo_elemento_despesa": elemento,
        "natureza_considerada": codigo_natureza_despesa_monitorado(natureza_codigo),
        "valor_empenhado_centavos": valor_para_centavos(
            registro.get("valor_empenhado"),
            "valor_empenhado",
        ),
        **_documentos_fornecedor(registro),
        "nome_fornecedor": _texto(registro.get("nome_negociante"), "nome_negociante", obrigatorio=False),
        "municipio_fornecedor_informado": _texto(
            registro.get("municipio_negociante"),
            "municipio_negociante",
            obrigatorio=False,
        ),
        "uf_fornecedor_informada": uf_informada.upper() if uf_informada else None,
        "estado_empenho": _texto(registro.get("estado_empenho"), "estado_empenho", obrigatorio=False),
        "numero_nota_anulacao_informado": _texto(
            registro.get("numero_nota_anulacao"),
            "numero_nota_anulacao",
            obrigatorio=False,
        ),
        "numero_empenho_substituto": _texto(
            registro.get("numero_empenho_substituto"),
            "numero_empenho_substituto",
            obrigatorio=False,
        ),
        "numero_contrato": _texto(registro.get("numero_contrato"), "numero_contrato", obrigatorio=False),
        "numero_licitacao": _texto(registro.get("numero_licitacao"), "numero_licitacao", obrigatorio=False),
        "payload_bruto": dict(registro),
    }


def mapear_nota_anulacao_empenho(registro: dict[str, Any]) -> dict[str, Any]:
    """Mapeia uma anulação e produz a mesma chave natural do empenho relacionado."""
    base = _campos_identificacao(registro, campo_numero="numero_nota_empenho")
    numero_anulacao = _texto(registro.get("numero_nota_anulacao"), "numero_nota_anulacao")
    assert numero_anulacao is not None
    data_anulacao = _data_civil(registro.get("data_anulacao"), "data_anulacao")
    chave_anulacao = "|".join((base["chave_empenho"], data_anulacao, numero_anulacao))

    return {
        **base,
        "tipo_registro": TIPO_REGISTRO_ANULACAO,
        "numero_anulacao": numero_anulacao,
        "data_anulacao": data_anulacao,
        "chave_anulacao": chave_anulacao,
        "modalidade_anulacao": _texto(
            registro.get("modalidade_anulacao"),
            "modalidade_anulacao",
            obrigatorio=False,
        ),
        "descricao_anulacao": _texto(
            registro.get("descricao_anulacao"),
            "descricao_anulacao",
            obrigatorio=False,
        ),
        "valor_anulacao_centavos": valor_para_centavos(
            registro.get("valor_anulacao"),
            "valor_anulacao",
        ),
        "payload_bruto": dict(registro),
    }


def mapear_notas_empenhos(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mapear_nota_empenho(registro) for registro in registros]


def mapear_notas_anulacoes_empenhos(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mapear_nota_anulacao_empenho(registro) for registro in registros]


__all__ = [
    "ContratoDespesaTceInvalido",
    "mapear_nota_anulacao_empenho",
    "mapear_nota_empenho",
    "mapear_notas_anulacoes_empenhos",
    "mapear_notas_empenhos",
    "valor_para_centavos",
]
