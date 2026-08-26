from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_ENV = {
    "TCE_CE_BASE_URL": "https://api-dados-abertos.tce.ce.gov.br/sim/",
    "IBGE_LOCALIDADES_BASE_URL": "https://servicodados.ibge.gov.br/api/v1/localidades",
    "PNCP_CONSULTA_BASE_URL": "https://pncp.gov.br/api/consulta",
    "PNCP_GESTAO_BASE_URL": "https://pncp.gov.br/api/pncp",
    "OPENCNPJ_BASE_URL": "https://kitana.opencnpj.com",
    "UF_PADRAO": "CE",
    "CODIGO_IBGE_PADRAO": "2304400",
    "CODIGO_MUNICIPIO_TCE_PADRAO": "010",
    "MODALIDADE_ID_PADRAO": "6",
}


class ConfigTest(unittest.TestCase):
    def _reload_config_with_env(self, env: dict[str, str]):
        module_name = "app.core.config"
        original_config = sys.modules.get(module_name)
        sys.modules.pop(module_name, None)

        try:
            with patch.dict(os.environ, env, clear=True):
                return importlib.import_module(module_name)
        finally:
            sys.modules.pop(module_name, None)
            if original_config is not None:
                sys.modules[module_name] = original_config

    def test_variavel_obrigatoria_ausente_falha_sem_default(self) -> None:
        env = REQUIRED_ENV.copy()
        env.pop("TCE_CE_BASE_URL")

        with self.assertRaisesRegex(RuntimeError, "TCE_CE_BASE_URL"):
            self._reload_config_with_env(env)

    def test_variaveis_configuradas_sao_carregadas(self) -> None:
        config = self._reload_config_with_env(REQUIRED_ENV)

        self.assertEqual(config.TCE_CE_BASE_URL, "https://api-dados-abertos.tce.ce.gov.br/sim")
        self.assertEqual(config.MODALIDADE_ID_PADRAO, 6)


if __name__ == "__main__":
    unittest.main()
