from fastapi import APIRouter, Depends, HTTPException, status

from app.backends.base import ProviderProject
from app.backends.registry import BackendRegistry
from app.routes.deps import require_api_key


def build_providers_router(
    *,
    api_key: str,
    registry: BackendRegistry,
) -> APIRouter:
    router = APIRouter(
        prefix="/providers",
        tags=["providers"],
        dependencies=[Depends(require_api_key(api_key))],
    )

    @router.get("/{provider}/projects", response_model=list[ProviderProject])
    async def list_remote_projects(provider: str):
        try:
            backend = registry.get(provider)
        except KeyError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"unknown provider '{provider}'",
            )
        return await backend.list_projects()

    return router
