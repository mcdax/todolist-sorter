from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel

from app.models import SortingProject


class Task(BaseModel):
    id: str
    content: str


class ProviderProject(BaseModel):
    id: str
    name: str


@runtime_checkable
class TaskBackend(Protocol):
    name: ClassVar[str]

    async def get_tasks(self, project: SortingProject) -> list[Task]: ...
    async def reorder(
        self, project: SortingProject, ordered_ids: list[str]
    ) -> None: ...
    async def update_task_content(
        self, project: SortingProject, task_id: str, new_content: str
    ) -> None: ...
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool: ...
    def extract_project_id(self, payload: dict) -> str | None: ...
    def extract_trigger_content(self, payload: dict) -> str | None: ...
    def extract_event_name(self, payload: dict) -> str | None: ...
    def extract_item_id(self, payload: dict) -> str | None: ...
    async def list_projects(self) -> list[ProviderProject]: ...
