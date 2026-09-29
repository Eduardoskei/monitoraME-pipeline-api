from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/monitorame_test",
    "LOG_DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test",
    "TCE_CE_BASE_URL": "https://api-dados-abertos.tce.ce.gov.br/sim/",
    "IBGE_LOCALIDADES_BASE_URL": "https://servicodados.ibge.gov.br/api/v1/localidades",
    "OPENCNPJ_BASE_URL": "https://api.opencnpj.org",
    "UF_PADRAO": "CE",
    "CODIGO_MUNICIPIO_TCE_PADRAO": "010",
}


class ConfigTest(unittest.TestCase):
    def _reload_config_with_env(self, env: dict[str, str]):
        module_name = "app.core.config"
        original_config = sys.modules.get(module_name)
        sys.modules.pop(module_name, None)

        isolated_env = {"PYTHON_DOTENV_DISABLED": "1", **env}

        try:
            with patch.dict(os.environ, isolated_env, clear=True):
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

    def test_database_url_obrigatoria(self) -> None:
        env = REQUIRED_ENV.copy()
        env.pop("DATABASE_URL")

        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            self._reload_config_with_env(env)

    def test_log_database_url_obrigatoria(self) -> None:
        env = REQUIRED_ENV.copy()
        env.pop("LOG_DATABASE_URL")

        with self.assertRaisesRegex(RuntimeError, "LOG_DATABASE_URL"):
            self._reload_config_with_env(env)

    def test_variaveis_configuradas_sao_carregadas(self) -> None:
        config = self._reload_config_with_env(REQUIRED_ENV)

        self.assertEqual(config.DATABASE_URL, "postgresql://postgres:postgres@localhost:5432/monitorame_test")
        self.assertEqual(
            config.LOG_DATABASE_URL,
            "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test",
        )
        self.assertEqual(config.TCE_CE_BASE_URL, "https://api-dados-abertos.tce.ce.gov.br/sim")
        self.assertEqual(len(config.NATUREZAS_DESPESA_CONSIDERADAS), 7)
        self.assertEqual(
            config.CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS,
            ("39", "51", "30", "52", "32", "35", "40"),
        )



if __name__ == "__main__":
    unittest.main()
