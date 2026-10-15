"""SQLAlchemy 数据库初始化与事务边界。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine


class Database:
    """持有 Engine 和 Session 工厂。

    每次 ``session()`` 调用都是独立事务：正常退出提交，异常退出回滚。
    Repository 本身不隐式提交，因此一阶段的状态、行动和事件可以原子写入。
    """

    def __init__(self, url: str | URL, *, echo: bool = False) -> None:
        database_url = make_url(url)
        connect_args: dict[str, object] = {}
        if database_url.drivername == "sqlite":
            connect_args["check_same_thread"] = False

        self.engine: Engine = create_engine(
            database_url,
            echo=echo,
            future=True,
            connect_args=connect_args,
        )
        if database_url.drivername == "sqlite":
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)

        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    @classmethod
    def sqlite(cls, path: str | Path, *, echo: bool = False) -> Database:
        resolved = Path(path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{resolved.as_posix()}", echo=echo)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            with session.begin():
                yield session
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
