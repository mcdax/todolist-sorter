import pytest
from pydantic_ai.models.test import TestModel

from app.backends.base import Task
from app.sorter import Assignment, CategorizedItems, categorize, render_prompt


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
