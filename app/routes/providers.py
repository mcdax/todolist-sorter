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

    @router.get(
        "/{provider}/projects",
        response_model=list[ProviderProject],
        summary="List remote provider-side projects",
        description=(
            "Calls the provider's API (e.g. Todoist) to enumerate every "
            "project the configured credentials can access. Used by the "
            "CLI's interactive `projects create` picker so users don't "
            "have to copy the numeric project id by hand."
        ),
        responses={
            200: {"description": "List of remote projects."},
            404: {"description": "Unknown provider id in URL path."},
        },
    )
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
