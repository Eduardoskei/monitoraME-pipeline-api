from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import DATABASE_URL, LOG_DATABASE_URL


DATABASE_SSLMODE = os.getenv("DATABASE_SSLMODE", "require")
LOG_DATABASE_SSLMODE = os.getenv("LOG_DATABASE_SSLMODE") or os.getenv("DATABASE_SSLMODE", "require")


class MainBase(DeclarativeBase):
    pass


class LogBase(DeclarativeBase):
    pass


_main_engine: Engine | None = None
_log_engine: Engine | None = None
_main_session_factory: sessionmaker[Session] | None = None
_log_session_factory: sessionmaker[Session] | None = None


def _psycopg_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def main_database_url() -> str:
    database_url = os.getenv("DATABASE_URL") or DATABASE_URL
    if not database_url.strip():
        raise RuntimeError("DATABASE_URL nao esta definida")
    return _psycopg_database_url(database_url.strip())


def log_database_url() -> str:
    database_url = os.getenv("LOG_DATABASE_URL") or LOG_DATABASE_URL
    if not database_url.strip():
        raise RuntimeError("LOG_DATABASE_URL nao esta definida")
    return _psycopg_database_url(database_url.strip())


def main_connect_args() -> dict[str, str]:
    return _connect_args(main_database_url(), os.getenv("DATABASE_SSLMODE") or DATABASE_SSLMODE or "require")


def log_connect_args() -> dict[str, str]:
    sslmode = os.getenv("LOG_DATABASE_SSLMODE") or LOG_DATABASE_SSLMODE or "require"
    return _connect_args(log_database_url(), sslmode)


def _connect_args(database_url: str, sslmode: str) -> dict[str, str]:
    if database_url.startswith("postgresql"):
        return {"sslmode": sslmode.strip()}
    return {}


def _engine(database_url: str, connect_args: dict[str, str]) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=0,
        connect_args=connect_args,
    )


def get_main_engine() -> Engine:
    global _main_engine

    if _main_engine is None:
        _main_engine = _engine(main_database_url(), main_connect_args())
    return _main_engine


def get_log_engine() -> Engine:
    global _log_engine

    if _log_engine is None:
        _log_engine = _engine(log_database_url(), log_connect_args())
    return _log_engine


def get_main_session_factory() -> sessionmaker[Session]:
    global _main_session_factory

    if _main_session_factory is None:
        _main_session_factory = sessionmaker(
            bind=get_main_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _main_session_factory


def get_log_session_factory() -> sessionmaker[Session]:
    global _log_session_factory

    if _log_session_factory is None:
        _log_session_factory = sessionmaker(
            bind=get_log_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _log_session_factory


def new_main_session() -> Session:
    return get_main_session_factory()()


def new_log_session() -> Session:
    return get_log_session_factory()()


@contextmanager
def main_session() -> Iterator[Session]:
    session = new_main_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def log_session() -> Iterator[Session]:
    session = new_log_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engines() -> None:
    global _main_engine, _log_engine, _main_session_factory, _log_session_factory

    if _main_engine is not None:
        _main_engine.dispose()
    if _log_engine is not None:
        _log_engine.dispose()
    _main_engine = None
    _log_engine = None
    _main_session_factory = None
    _log_session_factory = None


def dispose_main_engine() -> None:
    global _main_engine, _main_session_factory

    if _main_engine is not None:
        _main_engine.dispose()
    _main_engine = None
    _main_session_factory = None


def dispose_log_engine() -> None:
    global _log_engine, _log_session_factory

    if _log_engine is not None:
        _log_engine.dispose()
    _log_engine = None
    _log_session_factory = None


__all__ = [
    "LogBase",
    "MainBase",
    "dispose_engines",
    "dispose_log_engine",
    "dispose_main_engine",
    "get_log_engine",
    "get_log_session_factory",
    "get_main_engine",
    "get_main_session_factory",
    "log_connect_args",
    "log_database_url",
    "log_session",
    "main_connect_args",
    "main_database_url",
    "main_session",
    "new_log_session",
    "new_main_session",
]
