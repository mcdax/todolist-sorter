from uuid import UUID

from sqlmodel import Session, select

from app.models import CategoryCache

AUTH = {"X-API-Key": "testkey"}


def _create(client, cats):
    return client.post("/projects", json={
        "name": "P", "provider": "todoist",
        "external_project_id": "1", "categories": cats,
    }, headers=AUTH).json()


def _seed(session, pid, entries):
    pid = UUID(pid) if isinstance(pid, str) else pid
    for ckey, cat in entries:
        session.add(CategoryCache(project_id=pid, content_key=ckey,
                                  category_name=cat))
    session.commit()


def test_list_categories(client):
    p = _create(client, ["A", "B"])
    r = client.get(f"/projects/{p['id']}/categories", headers=AUTH)
    assert r.json() == ["A", "B"]


def test_add_clears_cache_and_triggers_sort(
    client, engine, sort_trigger_spy
):
    calls, _ = sort_trigger_spy
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A"), ("lettuce", "B")])

    r = client.post(f"/projects/{p['id']}/categories",
                    json={"name": "C"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == ["A", "B", "C"]

    with Session(engine) as s:
        assert s.exec(select(CategoryCache)).all() == []
    assert len(calls) == 1


def test_add_at_index(client):
    p = _create(client, ["A", "B"])
    r = client.post(f"/projects/{p['id']}/categories",
                    json={"name": "X", "at_index": 0}, headers=AUTH)
    assert r.json() == ["X", "A", "B"]


def test_remove_partial_invalidation(client, engine, sort_trigger_spy):
    calls, _ = sort_trigger_spy
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A"), ("lettuce", "B")])

    r = client.delete(f"/projects/{p['id']}/categories/0", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == ["B"]

    with Session(engine) as s:
        rows = s.exec(select(CategoryCache)).all()
        assert [row.category_name for row in rows] == ["B"]
    assert len(calls) == 1


def test_rename_clears_full_cache(client, engine, sort_trigger_spy):
    calls, _ = sort_trigger_spy
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A"), ("lettuce", "B")])

    r = client.patch(f"/projects/{p['id']}/categories/0",
                     json={"name": "AAA"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == ["AAA", "B"]

    with Session(engine) as s:
        assert s.exec(select(CategoryCache)).all() == []
    assert len(calls) == 1


def test_reorder_only_keeps_cache(client, engine, sort_trigger_spy):
    calls, _ = sort_trigger_spy
    p = _create(client, ["A", "B", "C"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A")])

    r = client.patch(f"/projects/{p['id']}/categories/0",
                     json={"move_to": 2}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == ["B", "C", "A"]

    with Session(engine) as s:
        assert len(s.exec(select(CategoryCache)).all()) == 1
    assert len(calls) == 1


def test_replace_with_add_clears_cache(client, engine):
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A")])

    r = client.put(f"/projects/{p['id']}/categories",
                   json={"categories": ["A", "B", "C"]}, headers=AUTH)
    assert r.status_code == 200
    with Session(engine) as s:
        assert s.exec(select(CategoryCache)).all() == []


def test_replace_only_removes_deletes_cache_of_removed(client, engine):
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A"), ("lettuce", "B")])

    r = client.put(f"/projects/{p['id']}/categories",
                   json={"categories": ["A"]}, headers=AUTH)
    assert r.status_code == 200
    with Session(engine) as s:
        rows = s.exec(select(CategoryCache)).all()
        assert [r.category_name for r in rows] == ["A"]
