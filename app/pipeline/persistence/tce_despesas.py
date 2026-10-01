"""Persistência versionada e publicação atômica das despesas do TCE-CE."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Iterable

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core import database, orm
from app.core.models import TceAnulacaoEmpenho, TceDespesaIngestionRun, TceEmpenho


STATUS_EM_VALIDACAO = "EM_VALIDACAO"
STATUS_PUBLICADO = "PUBLICADO"
STATUS_SUBSTITUIDO = "SUBSTITUIDO"
STATUS_REJEITADO = "REJEITADO"
RETENCAO_LOTES_SUBSTITUIDOS = timedelta(days=7)


@dataclass(frozen=True)
class LoteTceValidado:
    empenhos: list[dict[str, Any]]
    anulacoes: list[dict[str, Any]]
    problemas: list[dict[str, Any]]

    @property
    def valido(self) -> bool:
        return not self.problemas


@dataclass(frozen=True)
class ResultadoPublicacaoTce:
    run_id: int
    status: str
    quantidade_empenhos: int
    quantidade_anulacoes: int
    problemas: list[dict[str, Any]]
    run_anterior_id: int | None = None


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _payload_json(valor: Any) -> Any:
    return json.loads(json.dumps(valor, ensure_ascii=False, default=str))


def _problema(codigo: str, mensagem: str, **contexto: Any) -> dict[str, Any]:
    return {"codigo": codigo, "mensagem": mensagem, **contexto}


def _deduplicar(
    registros: Iterable[dict[str, Any]],
    *,
    campo_chave: str,
    tipo: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unicos: dict[str, dict[str, Any]] = {}
    problemas: list[dict[str, Any]] = []
    for registro in registros:
        chave = str(registro.get(campo_chave) or "").strip()
        if not chave:
            problemas.append(_problema("CHAVE_AUSENTE", f"{tipo} sem {campo_chave}."))
            continue
        anterior = unicos.get(chave)
        if anterior is None:
            unicos[chave] = registro
        elif anterior != registro:
            problemas.append(
                _problema(
                    "DUPLICIDADE_CONFLITANTE",
                    f"{tipo} repetido com conteúdos diferentes.",
                    chave=chave,
                )
            )
    return list(unicos.values()), problemas


def validar_lote_tce(
    *,
    codigo_municipio_tce: str,
    competencia: str,
    empenhos: Iterable[dict[str, Any]],
    anulacoes: Iterable[dict[str, Any]],
    valores_empenhos_publicados: dict[str, int] | None = None,
    anulacoes_publicadas_centavos: dict[str, int] | None = None,
) -> LoteTceValidado:
    """Deduplica e valida o lote contra os demais lotes atualmente publicados."""
    empenhos_unicos, problemas_empenhos = _deduplicar(
        empenhos,
        campo_chave="chave_empenho",
        tipo="Empenho",
    )
    anulacoes_unicas, problemas_anulacoes = _deduplicar(
        anulacoes,
        campo_chave="chave_anulacao",
        tipo="Anulação",
    )
    problemas = [*problemas_empenhos, *problemas_anulacoes]

    for tipo, registros in (("Empenho", empenhos_unicos), ("Anulação", anulacoes_unicas)):
        for registro in registros:
            if registro.get("codigo_municipio_tce") != codigo_municipio_tce:
                problemas.append(
                    _problema(
                        "MUNICIPIO_DIVERGENTE",
                        f"{tipo} pertence a outro município.",
                        chave=registro.get("chave_empenho"),
                    )
                )
            if registro.get("competencia") != competencia:
                problemas.append(
                    _problema(
                        "COMPETENCIA_DIVERGENTE",
                        f"{tipo} pertence a outra competência.",
                        chave=registro.get("chave_empenho"),
                    )
                )

    valores_empenhos = dict(valores_empenhos_publicados or {})
    for empenho in empenhos_unicos:
        chave = str(empenho["chave_empenho"])
        valor = int(empenho["valor_empenhado_centavos"])
        if valor < 0:
            problemas.append(
                _problema(
                    "VALOR_EMPENHO_NEGATIVO",
                    "Empenho possui valor negativo.",
                    chave=chave,
                    valor_centavos=valor,
                )
            )
        valores_empenhos[chave] = valor

    totais_anulacoes: defaultdict[str, int] = defaultdict(int)
    for chave, valor in (anulacoes_publicadas_centavos or {}).items():
        totais_anulacoes[chave] += int(valor)
    for anulacao in anulacoes_unicas:
        chave = str(anulacao["chave_empenho"])
        valor = int(anulacao["valor_anulacao_centavos"])
        if valor < 0:
            problemas.append(
                _problema(
                    "VALOR_ANULACAO_NEGATIVO",
                    "Anulação possui valor negativo.",
                    chave=chave,
                    valor_centavos=valor,
                )
            )
        totais_anulacoes[chave] += valor

    for chave, valor_anulado in totais_anulacoes.items():
        valor_empenhado = valores_empenhos.get(chave)
        if valor_empenhado is None:
            problemas.append(
                _problema(
                    "ANULACAO_ORFA",
                    "Anulação não possui empenho publicado ou presente no lote.",
                    chave=chave,
                )
            )
        elif valor_empenhado - valor_anulado < 0:
            problemas.append(
                _problema(
                    "VALOR_LIQUIDO_NEGATIVO",
                    "A soma das anulações supera o valor do empenho.",
                    chave=chave,
                    valor_empenhado_centavos=valor_empenhado,
                    valor_anulado_centavos=valor_anulado,
                )
            )

    return LoteTceValidado(
        empenhos=empenhos_unicos,
        anulacoes=anulacoes_unicas,
        problemas=problemas,
    )


def _bloquear_particao(session: Session, codigo_municipio_tce: str, competencia: str) -> None:
    # A validação soma anulações de competências diferentes. O lock precisa
    # serializar todas as publicações do município para essa leitura continuar
    # válida até o commit da transação.
    escopo = f"tce-despesas:{codigo_municipio_tce}"
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:escopo))"),
        {"escopo": escopo},
    )


def _lote_publicado_atual(
    session: Session,
    codigo_municipio_tce: str,
    competencia: str,
) -> TceDespesaIngestionRun | None:
    return session.scalar(
        select(TceDespesaIngestionRun)
        .where(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TceDespesaIngestionRun.competencia == competencia,
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
        )
        .with_for_update()
    )


def _carregar_referencias_publicadas(
    session: Session,
    codigo_municipio_tce: str,
    competencia_excluida: str,
) -> tuple[dict[str, int], dict[str, int]]:
    filtro_lotes = (
        TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
        TceDespesaIngestionRun.competencia != competencia_excluida,
        TceDespesaIngestionRun.status == STATUS_PUBLICADO,
    )
    empenhos_rows = session.execute(
        select(TceEmpenho.chave_empenho, TceEmpenho.valor_empenhado_centavos)
        .join(TceDespesaIngestionRun, TceEmpenho.run_id == TceDespesaIngestionRun.id)
        .where(*filtro_lotes)
    ).all()
    valores_empenhos: dict[str, int] = {}
    for chave, valor in empenhos_rows:
        valor_inteiro = int(valor)
        anterior = valores_empenhos.get(chave)
        if anterior is not None and anterior != valor_inteiro:
            raise RuntimeError(f"Empenho publicado com valores conflitantes: {chave}")
        valores_empenhos[chave] = valor_inteiro

    anulacoes_rows = session.execute(
        select(
            TceAnulacaoEmpenho.chave_empenho,
            func.sum(TceAnulacaoEmpenho.valor_anulacao_centavos),
        )
        .join(TceDespesaIngestionRun, TceAnulacaoEmpenho.run_id == TceDespesaIngestionRun.id)
        .where(*filtro_lotes)
        .group_by(TceAnulacaoEmpenho.chave_empenho)
    ).all()
    valores_anulacoes = {str(chave): int(valor) for chave, valor in anulacoes_rows}
    return valores_empenhos, valores_anulacoes


def _modelo_empenho(run_id: int, registro: dict[str, Any]) -> TceEmpenho:
    return TceEmpenho(
        run_id=run_id,
        chave_empenho=registro["chave_empenho"],
        exercicio_orcamento=int(registro["exercicio_orcamento"]),
        codigo_orgao=registro["codigo_orgao"],
        codigo_unidade_orcamentaria=registro["codigo_unidade_orcamentaria"],
        data_empenho=date.fromisoformat(registro["data_empenho"]),
        numero_empenho=registro["numero_empenho"],
        codigo_natureza_despesa=registro["codigo_natureza_despesa"],
        codigo_elemento_despesa=registro["codigo_elemento_despesa"],
        natureza_considerada=bool(registro["natureza_considerada"]),
        valor_empenhado_centavos=int(registro["valor_empenhado_centavos"]),
        tipo_documento_fornecedor=registro["tipo_documento_fornecedor"],
        documento_fornecedor=registro.get("documento_fornecedor"),
        cnpj_fornecedor=registro.get("cnpj_fornecedor"),
        cpf_fornecedor=registro.get("cpf_fornecedor"),
        nome_fornecedor=registro.get("nome_fornecedor"),
        municipio_fornecedor_informado=registro.get("municipio_fornecedor_informado"),
        uf_fornecedor_informada=registro.get("uf_fornecedor_informada"),
        estado_empenho=registro.get("estado_empenho"),
        numero_nota_anulacao_informado=registro.get("numero_nota_anulacao_informado"),
        numero_empenho_substituto=registro.get("numero_empenho_substituto"),
        numero_contrato=registro.get("numero_contrato"),
        numero_licitacao=registro.get("numero_licitacao"),
        payload=_payload_json(registro.get("payload_bruto") or {}),
    )


def _modelo_anulacao(run_id: int, registro: dict[str, Any]) -> TceAnulacaoEmpenho:
    return TceAnulacaoEmpenho(
        run_id=run_id,
        chave_anulacao=registro["chave_anulacao"],
        chave_empenho=registro["chave_empenho"],
        data_empenho=date.fromisoformat(registro["data_empenho"]),
        numero_empenho=registro["numero_empenho"],
        numero_anulacao=registro["numero_anulacao"],
        data_anulacao=date.fromisoformat(registro["data_anulacao"]),
        modalidade_anulacao=registro.get("modalidade_anulacao"),
        descricao_anulacao=registro.get("descricao_anulacao"),
        valor_anulacao_centavos=int(registro["valor_anulacao_centavos"]),
        payload=_payload_json(registro.get("payload_bruto") or {}),
    )


def _remover_lotes_expirados_session(session: Session, agora: datetime) -> int:
    resultado = session.execute(
        delete(TceDespesaIngestionRun).where(
            TceDespesaIngestionRun.status.in_((STATUS_SUBSTITUIDO, STATUS_REJEITADO)),
            TceDespesaIngestionRun.expira_em.is_not(None),
            TceDespesaIngestionRun.expira_em <= agora,
        )
    )
    return int(resultado.rowcount or 0)


def remover_lotes_expirados(*, agora: datetime | None = None) -> int:
    database.init_db()
    instante = agora or _agora_utc()
    with orm.main_session() as session:
        return _remover_lotes_expirados_session(session, instante)


def listar_publicacoes_competencias(
    *,
    codigo_municipio_tce: str,
    competencias: Iterable[str],
) -> dict[str, datetime | None]:
    """Retorna o instante da publicacao ativa de cada competencia solicitada."""
    competencias_unicas = tuple(dict.fromkeys(str(item) for item in competencias))
    if not competencias_unicas:
        return {}

    database.init_db()
    consulta = select(
        TceDespesaIngestionRun.competencia,
        TceDespesaIngestionRun.publicado_em,
    ).where(
        TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
        TceDespesaIngestionRun.status == STATUS_PUBLICADO,
        TceDespesaIngestionRun.competencia.in_(competencias_unicas),
    )
    with orm.main_session() as session:
        registros = session.execute(consulta).all()

    return {str(competencia): publicado_em for competencia, publicado_em in registros}


def listar_empenhos_liquidos_publicados(
    *,
    codigo_municipio_tce: str,
    data_inicial: date,
    data_final: date,
) -> list[dict[str, Any]]:
    """Lê empenhos ativos e desconta todas as anulações atualmente publicadas."""
    if data_inicial > data_final:
        raise ValueError("data_inicial deve ser menor ou igual a data_final.")

    database.init_db()
    anulacoes_publicadas = (
        select(
            TceAnulacaoEmpenho.chave_empenho.label("chave_empenho"),
            func.sum(TceAnulacaoEmpenho.valor_anulacao_centavos).label(
                "valor_anulado_centavos"
            ),
        )
        .join(
            TceDespesaIngestionRun,
            TceAnulacaoEmpenho.run_id == TceDespesaIngestionRun.id,
        )
        .where(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
        )
        .group_by(TceAnulacaoEmpenho.chave_empenho)
        .subquery()
    )
    valor_anulado = func.coalesce(anulacoes_publicadas.c.valor_anulado_centavos, 0)
    consulta = (
        select(
            TceEmpenho.chave_empenho,
            TceEmpenho.exercicio_orcamento,
            TceEmpenho.codigo_orgao,
            TceEmpenho.codigo_unidade_orcamentaria,
            TceEmpenho.data_empenho,
            TceEmpenho.numero_empenho,
            TceEmpenho.codigo_natureza_despesa,
            TceEmpenho.codigo_elemento_despesa,
            TceEmpenho.valor_empenhado_centavos,
            valor_anulado.label("valor_anulado_centavos"),
            (TceEmpenho.valor_empenhado_centavos - valor_anulado).label(
                "valor_liquido_centavos"
            ),
            TceEmpenho.tipo_documento_fornecedor,
            TceEmpenho.documento_fornecedor,
            TceEmpenho.cnpj_fornecedor,
            TceEmpenho.cpf_fornecedor,
            TceEmpenho.nome_fornecedor,
            TceEmpenho.municipio_fornecedor_informado,
            TceEmpenho.uf_fornecedor_informada,
            TceEmpenho.estado_empenho,
        )
        .join(
            TceDespesaIngestionRun,
            TceEmpenho.run_id == TceDespesaIngestionRun.id,
        )
        .outerjoin(
            anulacoes_publicadas,
            anulacoes_publicadas.c.chave_empenho == TceEmpenho.chave_empenho,
        )
        .where(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
            TceEmpenho.natureza_considerada.is_(True),
            TceEmpenho.data_empenho >= data_inicial,
            TceEmpenho.data_empenho <= data_final,
        )
        .order_by(TceEmpenho.data_empenho, TceEmpenho.chave_empenho)
    )

    with orm.main_session() as session:
        registros = session.execute(consulta).mappings().all()

    return [
        {
            **dict(registro),
            "codigo_municipio_tce": codigo_municipio_tce,
            "data_empenho": registro["data_empenho"].isoformat(),
            "valor_empenhado_centavos": int(registro["valor_empenhado_centavos"]),
            "valor_anulado_centavos": int(registro["valor_anulado_centavos"]),
            "valor_liquido_centavos": int(registro["valor_liquido_centavos"]),
        }
        for registro in registros
    ]


def registrar_lote_rejeitado(
    *,
    codigo_municipio_tce: str,
    competencia: str,
    erro: str,
    iniciado_em: datetime | None = None,
) -> ResultadoPublicacaoTce:
    """Registra falha de coleta/mapeamento sem alterar o lote publicado."""
    database.init_db()
    inicio = iniciado_em or _agora_utc()
    agora = _agora_utc()
    problema = _problema("FALHA_INGESTAO", erro)
    with orm.main_session() as session:
        _bloquear_particao(session, codigo_municipio_tce, competencia)
        _remover_lotes_expirados_session(session, agora)
        lote_anterior = _lote_publicado_atual(session, codigo_municipio_tce, competencia)
        run = TceDespesaIngestionRun(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            status=STATUS_REJEITADO,
            iniciado_em=inicio,
            finalizado_em=agora,
            expira_em=agora + RETENCAO_LOTES_SUBSTITUIDOS,
            quantidade_empenhos=0,
            quantidade_anulacoes=0,
            problemas=[problema],
            erro=erro,
        )
        session.add(run)
        session.flush()
        return ResultadoPublicacaoTce(
            run_id=run.id,
            status=run.status,
            quantidade_empenhos=0,
            quantidade_anulacoes=0,
            problemas=[problema],
            run_anterior_id=lote_anterior.id if lote_anterior else None,
        )


def publicar_lote_tce(
    *,
    codigo_municipio_tce: str,
    competencia: str,
    empenhos: Iterable[dict[str, Any]],
    anulacoes: Iterable[dict[str, Any]],
    iniciado_em: datetime | None = None,
) -> ResultadoPublicacaoTce:
    """Valida, persiste e publica uma competência em uma única transação."""
    database.init_db()
    inicio = iniciado_em or _agora_utc()
    agora = _agora_utc()

    with orm.main_session() as session:
        _bloquear_particao(session, codigo_municipio_tce, competencia)
        _remover_lotes_expirados_session(session, agora)
        lote_anterior = _lote_publicado_atual(session, codigo_municipio_tce, competencia)
        valores_publicados, anulacoes_publicadas = _carregar_referencias_publicadas(
            session,
            codigo_municipio_tce,
            competencia,
        )
        lote = validar_lote_tce(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            empenhos=empenhos,
            anulacoes=anulacoes,
            valores_empenhos_publicados=valores_publicados,
            anulacoes_publicadas_centavos=anulacoes_publicadas,
        )

        run = TceDespesaIngestionRun(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            status=STATUS_EM_VALIDACAO,
            iniciado_em=inicio,
            quantidade_empenhos=len(lote.empenhos),
            quantidade_anulacoes=len(lote.anulacoes),
            problemas=_payload_json(lote.problemas),
        )
        session.add(run)
        session.flush()

        if not lote.valido:
            run.status = STATUS_REJEITADO
            run.finalizado_em = agora
            run.expira_em = agora + RETENCAO_LOTES_SUBSTITUIDOS
            session.flush()
            return ResultadoPublicacaoTce(
                run_id=run.id,
                status=run.status,
                quantidade_empenhos=len(lote.empenhos),
                quantidade_anulacoes=len(lote.anulacoes),
                problemas=lote.problemas,
                run_anterior_id=lote_anterior.id if lote_anterior else None,
            )

        session.add_all(_modelo_empenho(run.id, registro) for registro in lote.empenhos)
        session.add_all(_modelo_anulacao(run.id, registro) for registro in lote.anulacoes)
        session.flush()

        if lote_anterior is not None:
            lote_anterior.status = STATUS_SUBSTITUIDO
            lote_anterior.substituido_em = agora
            lote_anterior.expira_em = agora + RETENCAO_LOTES_SUBSTITUIDOS
            session.flush()

        run.status = STATUS_PUBLICADO
        run.finalizado_em = agora
        run.publicado_em = agora
        session.flush()

        return ResultadoPublicacaoTce(
            run_id=run.id,
            status=run.status,
            quantidade_empenhos=len(lote.empenhos),
            quantidade_anulacoes=len(lote.anulacoes),
            problemas=[],
            run_anterior_id=lote_anterior.id if lote_anterior else None,
        )


__all__ = [
    "LoteTceValidado",
    "ResultadoPublicacaoTce",
    "listar_empenhos_liquidos_publicados",
    "listar_publicacoes_competencias",
    "publicar_lote_tce",
    "registrar_lote_rejeitado",
    "remover_lotes_expirados",
    "validar_lote_tce",
]
