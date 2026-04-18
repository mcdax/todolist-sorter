from app.backends.base import TaskBackend


class BackendRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, TaskBackend] = {}

    def register(self, backend: TaskBackend) -> None:
        if backend.name in self._by_name:
            raise ValueError(f"backend '{backend.name}' already registered")
        self._by_name[backend.name] = backend

    def get(self, name: str) -> TaskBackend:
        if name not in self._by_name:
            raise KeyError(f"unknown backend '{name}'")
        return self._by_name[name]

    def names(self) -> list[str]:
        return list(self._by_name)
