from fastapi import Header, HTTPException, status


def require_api_key(expected: str):
    async def _dep(x_api_key: str | None = Header(default=None)):
        if x_api_key != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid API key",
            )
    return _dep
