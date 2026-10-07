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

    def test_migration_adiciona_logs_operacionais_da_aplicacao(self) -> None:
        migration = ROOT / "alembic" / "logs" / "versions" / "0002_application_logs.py"
        modulo = _carregar_migration(migration)
        texto = migration.read_text(encoding="utf-8")

        self.assertEqual(modulo.revision, "0002_application_logs")
        self.assertEqual(modulo.down_revision, "0001_initial_logs")
        self.assertIn('"logs_aplicacao"', texto)
        self.assertIn("idx_logs_aplicacao_nivel_criado", texto)
        self.assertTrue(callable(modulo.upgrade))
        self.assertTrue(callable(modulo.downgrade))

    def test_migration_tce_encadeia_schema_versionado(self) -> None:
        migration = (
            ROOT
            / "alembic"
            / "main"
            / "versions"
            / "0002_tce_despesas_versionadas.py"
        )
        modulo = _carregar_migration(migration)
        texto = migration.read_text(encoding="utf-8")

        self.assertEqual(modulo.revision, "0002_tce_despesas")
        self.assertEqual(modulo.down_revision, "0001_initial_main")
        self.assertIn("tce_despesa_ingestion_runs", texto)
        self.assertIn("tce_empenhos", texto)
        self.assertIn("tce_anulacoes_empenhos", texto)
        self.assertIn("status = 'PUBLICADO'", texto)
        self.assertTrue(callable(modulo.upgrade))
        self.assertTrue(callable(modulo.downgrade))

    def test_migration_remove_tabelas_do_modulo_pncp(self) -> None:
        migration = (
            ROOT
            / "alembic"
            / "main"
            / "versions"
            / "0003_remove_pncp.py"
        )
        modulo = _carregar_migration(migration)

        self.assertEqual(modulo.revision, "0003_remove_pncp")
        self.assertEqual(modulo.down_revision, "0002_tce_despesas")
        self.assertEqual(
            set(modulo.PNCP_TABLES),
            {
                "pncp_ingestion_state",
                "pncp_ingestion_runs",
                "pncp_contratacoes",
                "pncp_itens",
                "pncp_resultados",
                "pncp_contratos",
                "pncp_pca_planos",
                "pncp_pca_itens",
                "pncp_fornecedores",
            },
        )
        self.assertTrue(callable(modulo.upgrade))
        self.assertTrue(callable(modulo.downgrade))

    def test_migration_adiciona_mapeamento_de_municipios_tce(self) -> None:
        migration = (
            ROOT
            / "alembic"
            / "main"
            / "versions"
            / "0004_tce_municipios.py"
        )
        modulo = _carregar_migration(migration)
        texto = migration.read_text(encoding="utf-8")

        self.assertEqual(modulo.revision, "0004_tce_municipios")
        self.assertEqual(modulo.down_revision, "0003_remove_pncp")
        self.assertIn('"tce_municipios"', texto)
        self.assertIn('"codigo_municipio_tce"', texto)
        self.assertIn('"codigo_municipio_ibge"', texto)
        self.assertIn('sa.UniqueConstraint("codigo_municipio_ibge")', texto)
        self.assertTrue(callable(modulo.upgrade))
        self.assertTrue(callable(modulo.downgrade))

    def test_migration_adiciona_cache_completo_de_fornecedores(self) -> None:
        migration = (
            ROOT
            / "alembic"
            / "main"
            / "versions"
            / "0005_fornecedores_cache.py"
        )
        modulo = _carregar_migration(migration)
        texto = migration.read_text(encoding="utf-8")

        self.assertEqual(modulo.revision, "0005_fornecedores_cache")
        self.assertEqual(modulo.down_revision, "0004_tce_municipios")
        self.assertIn('"fornecedores_cache"', texto)
        self.assertIn('"dados_normalizados"', texto)
        self.assertIn('"payload"', texto)
        self.assertIn('"expira_em"', texto)
        self.assertTrue(callable(modulo.upgrade))
        self.assertTrue(callable(modulo.downgrade))

    def test_migration_adiciona_indice_de_empenhos_por_fornecedor(self) -> None:
        migration = (
            ROOT
            / "alembic"
            / "main"
            / "versions"
            / "0006_tce_empenhos_fornecedor_index.py"
        )
        modulo = _carregar_migration(migration)
        texto = migration.read_text(encoding="utf-8")

        self.assertEqual(modulo.revision, "0006_tce_empenhos_fornecedor_idx")
        self.assertLessEqual(len(modulo.revision), 32)
        self.assertEqual(modulo.down_revision, "0005_fornecedores_cache")
        self.assertIn('"idx_tce_empenhos_fornecedor_data"', texto)
        self.assertIn('"documento_fornecedor"', texto)
        self.assertIn('"data_empenho"', texto)
        self.assertTrue(callable(modulo.upgrade))
        self.assertTrue(callable(modulo.downgrade))


if __name__ == "__main__":
    unittest.main()
