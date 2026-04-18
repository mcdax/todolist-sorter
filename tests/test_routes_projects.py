AUTH = {"X-API-Key": "testkey"}


def test_create_project(client):
    r = client.post("/projects", json={
        "name": "Lidl", "provider": "todoist",
        "external_project_id": "999",
        "categories": ["🥬 Vegetables", "🍎 Fruit"],
    }, headers=AUTH)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Lidl"
    assert "id" in body


def test_list_projects(client):
    client.post("/projects", json={
        "name": "A", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH)
    r = client.get("/projects", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_get_project(client):
    p = client.post("/projects", json={
        "name": "A", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH).json()
    r = client.get(f"/projects/{p['id']}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["name"] == "A"


def test_update_project(client):
    p = client.post("/projects", json={
        "name": "A", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH).json()
    r = client.put(f"/projects/{p['id']}", json={
        "name": "B", "enabled": False,
        "debounce_seconds": 10, "description": "new",
    }, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "B"
    assert body["enabled"] is False
    assert body["debounce_seconds"] == 10
    assert body["description"] == "new"


def test_delete_project(client):
    p = client.post("/projects", json={
        "name": "A", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH).json()
    assert client.delete(f"/projects/{p['id']}", headers=AUTH).status_code == 204
    assert client.get(f"/projects/{p['id']}", headers=AUTH).status_code == 404


def test_auth_required(client):
    assert client.get("/projects").status_code == 401


def test_create_duplicate_returns_409(client):
    body = {
        "name": "A", "provider": "todoist",
        "external_project_id": "dup", "categories": [],
    }
    assert client.post("/projects", json=body, headers=AUTH).status_code == 201
    r = client.post("/projects", json=body, headers=AUTH)
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]
