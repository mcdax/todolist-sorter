from collections.abc import Iterator

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def create_db_and_tables(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


def get_session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
