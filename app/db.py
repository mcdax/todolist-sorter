from collections.abc import Iterator

from sqlalchemy import Engine, event, text
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


def ensure_columns(engine: Engine) -> None:
    """Add columns that were introduced after the first schema.

    SQLModel.metadata.create_all is CREATE-IF-NOT-EXISTS only; it won't
    ALTER existing tables. Without this, deployments built before these
    columns existed stay broken.
    """
    url = str(engine.url)
    if not url.startswith("sqlite"):
        return

    _desired: list[tuple[str, str, str]] = [
        ("sortingproject", "additional_instructions", "TEXT"),
        ("categorycache", "transformed_content", "TEXT"),
    ]

    with engine.connect() as conn:
        for table, column, col_type in _desired:
            rows = conn.execute(
                text(f"PRAGMA table_info({table})")
            ).fetchall()
            existing = {row[1] for row in rows}
            if column not in existing:
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                )
        conn.commit()


def get_session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
