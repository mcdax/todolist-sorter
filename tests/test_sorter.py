import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic_ai.models.test import TestModel
from sqlmodel import Session

from app.backends.base import Task
from app.models import CategoryCache, SortingProject
from app.sorter import (
    Assignment,
    CategorizedItems,
    categorize,
    compute_reorder,
    render_prompt,
    sort_project,
    validate_assignments,
)


def test_render_prompt_with_hits_and_misses():
    prompt = render_prompt(
        categories=["🥬 Vegetables", "🍎 Fruit"],
        description="Supermarket route",
        hits={"Apples": "🍎 Fruit"},
        misses=[Task(id="42", content="Cinnamon")],
    )
    assert "🥬 Vegetables" in prompt
    assert "🍎 Fruit" in prompt
    assert "Supermarket route" in prompt
    assert "Apples" in prompt
    assert "Cinnamon" in prompt
    assert "id=42" in prompt


def test_render_prompt_no_hits_omits_reference_block():
    prompt = render_prompt(
        categories=["A", "B"], description=None, hits={},
        misses=[Task(id="1", content="X")],
    )
    assert "Already assigned" not in prompt


def test_schemas():
    a = Assignment(item_id="1", category_name="A")
    c = CategorizedItems(assignments=[a])
    assert c.assignments[0].item_id == "1"


@pytest.mark.asyncio
async def test_categorize_with_test_model():
    fixed = {
        "assignments": [
            {"item_id": "1", "category_name": "🍎 Fruit"},
            {"item_id": "2", "category_name": "🥬 Vegetables"},
        ]
    }
    model = TestModel(custom_output_args=fixed)

    result = await categorize(
        model=model,
        categories=["🥬 Vegetables", "🍎 Fruit"],
        description=None,
        hits={},
        misses=[Task(id="1", content="Apples"), Task(id="2", content="Lettuce")],
    )

    ids = {a.item_id: a.category_name for a in result.assignments}
    assert ids == {"1": "🍎 Fruit", "2": "🥬 Vegetables"}


def test_validate_assignments_drops_invalid_category():
    raw = CategorizedItems(assignments=[
        Assignment(item_id="1", category_name="Fruit"),
        Assignment(item_id="2", category_name="MadeUp"),
    ])
    valid = validate_assignments(
        raw, categories=["Fruit", "Vegetables"], requested_ids={"1", "2"},
    )
    assert len(valid) == 1
    assert valid[0].item_id == "1"


def test_validate_assignments_drops_unknown_item():
    raw = CategorizedItems(assignments=[
        Assignment(item_id="99", category_name="Fruit"),
    ])
    valid = validate_assignments(
        raw, categories=["Fruit"], requested_ids={"1"},
    )
    assert valid == []


def test_validate_assignments_deduplicates_item_id():
    raw = CategorizedItems(assignments=[
        Assignment(item_id="1", category_name="Fruit"),
        Assignment(item_id="1", category_name="Vegetables"),
    ])
    valid = validate_assignments(
        raw, categories=["Fruit", "Vegetables"], requested_ids={"1"},
    )
    assert len(valid) == 1
    assert valid[0].category_name == "Fruit"


def test_compute_reorder_groups_by_category_preserves_intra_order():
    tasks = [
        Task(id="T1", content="Milk"),
        Task(id="T2", content="Apples"),
        Task(id="T3", content="Yogurt"),
        Task(id="T4", content="Lettuce"),
    ]
    categories = ["🥬 Vegetables", "🍎 Fruit", "🥛 Dairy"]
    assignments = {
        "T1": "🥛 Dairy",
        "T2": "🍎 Fruit",
        "T3": "🥛 Dairy",
        "T4": "🥬 Vegetables",
    }

    ordered = compute_reorder(tasks, categories, assignments)
    assert ordered == ["T4", "T2", "T1", "T3"]


def test_compute_reorder_orphans_go_to_end():
    tasks = [
        Task(id="T1", content="Cinnamon"),
        Task(id="T2", content="Apples"),
    ]
    ordered = compute_reorder(
        tasks, ["🍎 Fruit"], {"T2": "🍎 Fruit"},
    )
    assert ordered == ["T2", "T1"]


@pytest.mark.asyncio
async def test_sort_project_all_hits_skips_llm(session: Session):
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="Lidl", provider="todoist",
        external_project_id="999",
        categories=["🥬 Vegetables", "🍎 Fruit"],
    ))
    session.add(CategoryCache(project_id=pid, content_key="apples",
                              category_name="🍎 Fruit"))
    session.add(CategoryCache(project_id=pid, content_key="lettuce",
                              category_name="🥬 Vegetables"))
    session.commit()

    backend = MagicMock()
    backend.get_tasks = AsyncMock(return_value=[
        Task(id="T1", content="Apples"),
        Task(id="T2", content="Lettuce"),
    ])
    backend.reorder = AsyncMock()

    async def _spy(**_):
        raise AssertionError("LLM should not be called")

    reorder_callback_calls: list[tuple] = []

    def _on_reorder(pid_, ids):
        reorder_callback_calls.append((pid_, set(ids)))

    await sort_project(
        project_id=pid, session=session,
        backend=backend, llm_model="x",
        categorize_fn=_spy, on_reorder=_on_reorder,
    )

    args = backend.reorder.await_args.args
    assert args[1] == ["T2", "T1"]
    assert reorder_callback_calls == [(pid, {"T1", "T2"})]


@pytest.mark.asyncio
async def test_sort_project_partial_miss_calls_llm_and_writes_cache(session: Session):
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="Lidl", provider="todoist",
        external_project_id="999",
        categories=["🥬 Vegetables", "🍎 Fruit"],
    ))
    session.add(CategoryCache(project_id=pid, content_key="apples",
                              category_name="🍎 Fruit"))
    session.commit()

    backend = MagicMock()
    backend.get_tasks = AsyncMock(return_value=[
        Task(id="T1", content="Apples"),
        Task(id="T2", content="Cinnamon"),
    ])
    backend.reorder = AsyncMock()

    async def _llm(**kw):
        assert {t.id for t in kw["misses"]} == {"T2"}
        return CategorizedItems(assignments=[
            Assignment(item_id="T2", category_name="🍎 Fruit"),
        ])

    await sort_project(
        project_id=pid, session=session,
        backend=backend, llm_model="x",
        categorize_fn=_llm, on_reorder=lambda p, ids: None,
    )

    cinnamon = session.get(CategoryCache, (pid, "cinnamon"))
    assert cinnamon is not None
    assert cinnamon.category_name == "🍎 Fruit"
    backend.reorder.assert_awaited_once()


def test_render_prompt_with_additional_instructions():
    prompt = render_prompt(
        categories=["A", "B"],
        description=None,
        hits={},
        misses=[Task(id="1", content="Apples")],
        additional_instructions="fix obvious typos",
    )
    assert "fix obvious typos" in prompt
    assert "transformed_content" in prompt


def test_render_prompt_without_additional_instructions():
    prompt = render_prompt(
        categories=["A", "B"],
        description=None,
        hits={},
        misses=[Task(id="1", content="Apples")],
    )
    assert "transformed_content" not in prompt


@pytest.mark.asyncio
async def test_sort_project_applies_transformation_and_caches_it(session: Session):
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="Lidl", provider="todoist",
        external_project_id="999",
        categories=["🍎 Fruit"],
        additional_instructions="fix obvious typos",
    ))
    session.commit()

    backend = MagicMock()
    backend.get_tasks = AsyncMock(return_value=[
        Task(id="T1", content="Aplles"),
        Task(id="T2", content="Milk"),
    ])
    backend.reorder = AsyncMock()
    backend.update_task_content = AsyncMock()

    async def _llm(**kw):
        return CategorizedItems(assignments=[
            Assignment(item_id="T1", category_name="🍎 Fruit",
                       transformed_content="Apples"),
            Assignment(item_id="T2", category_name="🍎 Fruit",
                       transformed_content=None),
        ])

    update_calls: list[tuple] = []

    def _on_reorder(pid_, ids):
        pass

    await sort_project(
        project_id=pid, session=session,
        backend=backend, llm_model="x",
        categorize_fn=_llm, on_reorder=_on_reorder,
    )

    # update_task_content called for T1 (typo fixed), not T2 (no change)
    backend.update_task_content.assert_awaited_once()
    call_args = backend.update_task_content.await_args
    assert call_args.args[1] == "T1"
    assert call_args.args[2] == "Apples"

    # cache row has transformed_content populated
    row = session.get(CategoryCache, (pid, "aplles"))
    assert row is not None
    assert row.transformed_content == "Apples"


@pytest.mark.asyncio
async def test_sort_project_cached_transformation_applied_on_hit(session: Session):
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="Lidl", provider="todoist",
        external_project_id="999",
        categories=["🍎 Fruit"],
        additional_instructions="fix obvious typos",
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="aplles",
        category_name="🍎 Fruit", transformed_content="Apples",
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="milk",
        category_name="🍎 Fruit", transformed_content=None,
    ))
    session.commit()

    backend = MagicMock()
    backend.get_tasks = AsyncMock(return_value=[
        Task(id="T1", content="Aplles"),
        Task(id="T2", content="Milk"),
    ])
    backend.reorder = AsyncMock()
    backend.update_task_content = AsyncMock()

    async def _spy(**_):
        raise AssertionError("LLM should not be called on full cache hit")

    await sort_project(
        project_id=pid, session=session,
        backend=backend, llm_model="x",
        categorize_fn=_spy, on_reorder=lambda p, ids: None,
    )

    # update_task_content called for T1 (cached transform differs from content)
    backend.update_task_content.assert_awaited_once()
    call_args = backend.update_task_content.await_args
    assert call_args.args[1] == "T1"
    assert call_args.args[2] == "Apples"


@pytest.mark.asyncio
async def test_sort_project_suppression_covers_updated_ids(session: Session):
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="Lidl", provider="todoist",
        external_project_id="999",
        categories=["🍎 Fruit", "🥛 Dairy"],
        additional_instructions="fix typos",
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="aplles",
        category_name="🍎 Fruit", transformed_content="Apples",
    ))
    session.add(CategoryCache(
        project_id=pid, content_key="milk",
        category_name="🥛 Dairy", transformed_content=None,
    ))
    session.commit()

    backend = MagicMock()
    backend.get_tasks = AsyncMock(return_value=[
        Task(id="T1", content="Aplles"),
        Task(id="T2", content="Milk"),
    ])
    backend.reorder = AsyncMock()
    backend.update_task_content = AsyncMock()

    on_reorder_calls: list[tuple] = []

    def _on_reorder(pid_, ids):
        on_reorder_calls.append((pid_, set(ids)))

    async def _spy(**_):
        raise AssertionError("LLM should not be called")

    await sort_project(
        project_id=pid, session=session,
        backend=backend, llm_model="x",
        categorize_fn=_spy, on_reorder=_on_reorder,
    )

    assert len(on_reorder_calls) == 1
    _, affected = on_reorder_calls[0]
    # Both T1 (content update) and T2 (reorder) should be in affected
    assert "T1" in affected
    assert "T2" in affected


@pytest.mark.asyncio
async def test_sort_project_partial_miss_log_records(
    session: Session, caplog: pytest.LogCaptureFixture
):
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="Lidl", provider="todoist",
        external_project_id="999",
        categories=["🥬 Vegetables", "🍎 Fruit"],
    ))
    session.add(CategoryCache(project_id=pid, content_key="apples",
                              category_name="🍎 Fruit"))
    session.commit()

    backend = MagicMock()
    backend.get_tasks = AsyncMock(return_value=[
        Task(id="T1", content="Apples"),
        Task(id="T2", content="Cinnamon"),
    ])
    backend.reorder = AsyncMock()

    llm_category = "🍎 Fruit"

    async def _llm(**kw):
        return CategorizedItems(assignments=[
            Assignment(item_id="T2", category_name=llm_category),
        ])

    caplog.set_level(logging.INFO, logger="app.sorter")
    await sort_project(
        project_id=pid, session=session,
        backend=backend, llm_model="x",
        categorize_fn=_llm, on_reorder=lambda p, ids: None,
    )

    messages = [r.message for r in caplog.records]

    # cache hit line for Apples
    cache_hit_lines = [m for m in messages if "cache hit" in m]
    assert any("Apples" in m and "🍎 Fruit" in m for m in cache_hit_lines), (
        f"Expected a cache hit line with 'Apples' and '🍎 Fruit', got: {cache_hit_lines}"
    )

    # need LLM line for Cinnamon
    need_llm_lines = [m for m in messages if "need LLM" in m]
    assert any("Cinnamon" in m for m in need_llm_lines), (
        f"Expected a need-LLM line with 'Cinnamon', got: {need_llm_lines}"
    )

    # LLM categorized line for Cinnamon
    llm_cat_lines = [m for m in messages if "LLM categorized" in m]
    assert any("Cinnamon" in m and llm_category in m for m in llm_cat_lines), (
        f"Expected an LLM categorized line with 'Cinnamon' and '{llm_category}', got: {llm_cat_lines}"
    )

    # reordered line
    reorder_lines = [m for m in messages if "reordered" in m]
    assert reorder_lines, f"Expected a 'reordered' log line, got messages: {messages}"
