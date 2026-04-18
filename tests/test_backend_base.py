from app.backends.base import Task, TaskBackend


def test_task_model():
    t = Task(id="abc", content="Apples")
    assert t.id == "abc"
    assert t.content == "Apples"


def test_taskbackend_is_importable():
    assert TaskBackend is not None
