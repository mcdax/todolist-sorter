from sqlmodel import Session, select

from app.models import CategoryCache

AUTH = {"X-API-Key": "testkey"}


def _create_seeded(client, engine):
    p = client.post("/projects", json={
        "name": "P", "provider": "todoist",
        "external_project_id": "1", "categories": ["A"],
    }, headers=AUTH).json()
    with Session(engine) as s:
        s.add(CategoryCache(project_id=p["id"], content_key="apple",
                            category_name="A"))
        s.commit()
    return p


def test_get_cache(client, engine):
    p = _create_seeded(client, engine)
    r = client.get(f"/projects/{p['id']}/cache", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == [{"content_key": "apple", "category_name": "A"}]


def test_clear_cache(client, engine):
    p = _create_seeded(client, engine)
    r = client.delete(f"/projects/{p['id']}/cache", headers=AUTH)
    assert r.status_code == 204
    with Session(engine) as s:
        assert s.exec(select(CategoryCache)).all() == []


def test_manual_sort_triggers_callback(client, sort_trigger_spy):
    calls, _ = sort_trigger_spy
    p = client.post("/projects", json={
        "name": "P", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH).json()
    r = client.post(f"/projects/{p['id']}/sort", headers=AUTH)
    assert r.status_code == 202
    assert len(calls) == 1
