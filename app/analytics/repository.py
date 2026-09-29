"""Leitura de carga canonica validada; nao interpreta eventos brutos do TCE."""
import logging
import os
from pathlib import Path
from pydantic import ValidationError
from .models import Snapshot
from .service import AnalyticsError, validate_snapshot


logger = logging.getLogger(__name__)

def load_snapshot() -> Snapshot:
    for variable, fallback in (("ANALYTICS_SNAPSHOT_PATH", False), ("ANALYTICS_PREVIOUS_SNAPSHOT_PATH", True)):
        path = os.getenv(variable)
        if not path:
            continue
        try:
            snapshot = Snapshot.model_validate_json(Path(path).read_bytes())
            validate_snapshot(snapshot)
            if fallback:
                snapshot = snapshot.model_copy(update={"is_stale": True, "stale_reason": "CURRENT_LOAD_UNAVAILABLE"})
            return snapshot
        except AnalyticsError as error:
            logger.warning("Carga analitica rejeitada: %s", error.code)
        except (OSError, ValueError, ValidationError):
            logger.warning("Carga analitica indisponivel ou fora do schema canonico.")
    raise AnalyticsError("NO_VALID_LOAD", "Nenhuma carga valida de empenhos disponivel.")
