"""PostgreSQL / MySQL 通用 SQLAlchemy 2.0 engine + session。"""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import settings

_engine_options: dict = {"pool_pre_ping": True, "future": True}
if settings.db_kind == "sqlite":
    # SQLite（仅本地测试）使用内存库与 StaticPool，让多个 session 共享同一连接
    from sqlalchemy.pool import StaticPool

    _engine_options = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
else:
    _engine_options.update({"pool_size": 8, "max_overflow": 16, "pool_recycle": 1800})

engine = create_engine(settings.database_url, **_engine_options)


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ping_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def wait_for_db(max_wait_seconds: int = 90) -> bool:
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        if ping_db():
            return True
        time.sleep(2)
    return False
