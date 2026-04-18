from sqlmodel import Session

from app.db import create_db_and_tables, get_session, make_engine


def test_make_engine_and_create_tables():
    engine = make_engine("sqlite://")
    create_db_and_tables(engine)


def test_get_session_yields_session():
    engine = make_engine("sqlite://")
    create_db_and_tables(engine)
    gen = get_session(engine)
    sess = next(gen)
    assert isinstance(sess, Session)
    try:
        next(gen)
    except StopIteration:
        pass
