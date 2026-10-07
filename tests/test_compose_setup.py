from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ComposeSetupTest(unittest.TestCase):
    def test_compose_declara_postgres_efemero_e_healthcheck(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("postgres:16-alpine", compose)
        self.assertNotIn("volumes:", compose)
        self.assertIn("tmpfs:", compose)
        self.assertIn("/var/lib/postgresql/data", compose)
        self.assertIn("pg_isready", compose)

    def test_compose_constroi_api_e_executa_migrations_antes_do_start(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("dockerfile: Dockerfile", compose)
        self.assertIn('command: ["migrate"]', compose)
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertIn('"${API_PORT:-8000}:8000"', compose)

    def test_imagem_executa_com_usuario_nao_root_e_entrypoint(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("FROM python:3.14-slim", dockerfile)
        self.assertIn("USER app", dockerfile)
        self.assertIn('ENTRYPOINT ["./docker/entrypoint.sh"]', dockerfile)
        self.assertIn('if [ "${1:-}" = "migrate" ]', entrypoint)
        self.assertIn("alembic -n logs upgrade head", entrypoint)

    def test_exemplo_de_ambiente_aponta_para_postgres_local_sem_ssl(self) -> None:
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn(
            "DATABASE_URL=postgresql://monitorame:monitorame@localhost:5432/monitorame\n",
            env_example,
        )
        self.assertIn(
            "LOG_DATABASE_URL=postgresql://monitorame:monitorame@localhost:5432/monitorame\n",
            env_example,
        )
        self.assertIn("DATABASE_SSLMODE=disable", env_example)
        self.assertIn("LOG_DATABASE_SSLMODE=disable", env_example)


if __name__ == "__main__":
    unittest.main()
