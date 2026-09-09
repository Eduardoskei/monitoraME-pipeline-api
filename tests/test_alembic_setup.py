from __future__ import annotations

import configparser
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _carregar_migration(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Nao foi possivel carregar migration: {path}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class AlembicSetupTest(unittest.TestCase):
    def test_alembic_ini_declara_ambientes_principal_e_logs(self) -> None:
        config = configparser.RawConfigParser()
        config.read(ROOT / "alembic.ini")

        self.assertEqual(config.get("alembic", "script_location"), "%(here)s/alembic/main")
        self.assertEqual(config.get("logs", "script_location"), "%(here)s/alembic/logs")

    def test_ambientes_possuem_env_script_e_versions(self) -> None:
        for ambiente in ("main", "logs"):
            with self.subTest(ambiente=ambiente):
                self.assertTrue((ROOT / "alembic" / ambiente / "env.py").is_file())
                self.assertTrue((ROOT / "alembic" / ambiente / "script.py.mako").is_file())
                self.assertTrue((ROOT / "alembic" / ambiente / "versions").is_dir())

    def test_ambientes_usam_tabelas_de_versao_independentes(self) -> None:
        main_env = (ROOT / "alembic" / "main" / "env.py").read_text(encoding="utf-8")
        logs_env = (ROOT / "alembic" / "logs" / "env.py").read_text(encoding="utf-8")

        self.assertIn('version_table="alembic_version_main"', main_env)
        self.assertIn('version_table="alembic_version_logs"', logs_env)

    def test_migration_inicial_principal_e_destrutiva_por_decisao_explicita(self) -> None:
        migration = ROOT / "alembic" / "main" / "versions" / "0001_initial_main_schema.py"
        modulo = _carregar_migration(migration)
        texto = migration.read_text(encoding="utf-8")

        self.assertEqual(modulo.revision, "0001_initial_main")
        self.assertIsNone(modulo.down_revision)
        self.assertIn("DROP TABLE IF EXISTS pncp_contratacoes CASCADE", texto)
        self.assertIn("pncp_fornecedores", texto)
        self.assertTrue(callable(modulo.upgrade))
        self.assertTrue(callable(modulo.downgrade))

    def test_migration_inicial_logs_usa_ambiente_separado(self) -> None:
        migration = ROOT / "alembic" / "logs" / "versions" / "0001_initial_log_schema.py"
        modulo = _carregar_migration(migration)
        texto = migration.read_text(encoding="utf-8")

        self.assertEqual(modulo.revision, "0001_initial_logs")
        self.assertIsNone(modulo.down_revision)
        self.assertIn("DROP TABLE IF EXISTS logs_ingestao CASCADE", texto)
        self.assertIn("idx_logs_ingestao_fonte_inicio", texto)
        self.assertTrue(callable(modulo.upgrade))
        self.assertTrue(callable(modulo.downgrade))


if __name__ == "__main__":
    unittest.main()
