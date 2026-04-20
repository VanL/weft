"""Global registry for decorated Django tasks."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from django.apps import apps as django_apps
from django.utils.module_loading import autodiscover_modules

from weft_django.conf import get_autodiscover_module


class TaskRegistry:
    """Registry of named decorated Django tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, Any] = {}
        self._autodiscovered = False

    def register(self, task: Any) -> Any:
        existing = self._tasks.get(task.name)
        if existing is not None:
            if getattr(existing, "callable_ref", None) == getattr(
                task, "callable_ref", None
            ):
                return existing
            raise RuntimeError(f"Duplicate weft task name: {task.name}")
        self._tasks[task.name] = task
        return task

    def get_task(self, name: str) -> Any:
        try:
            return self._tasks[name]
        except KeyError as exc:
            raise KeyError(f"Unknown weft task: {name}") from exc

    def iter_tasks(self) -> Iterator[Any]:
        return iter(self._tasks.values())

    def is_registered(self, name: str) -> bool:
        return name in self._tasks

    def autodiscover(self) -> None:
        if self._autodiscovered:
            return
        autodiscover_modules(get_autodiscover_module())
        self._autodiscovered = True

    def clear(self) -> None:
        self._tasks.clear()
        self._autodiscovered = False


registry = TaskRegistry()


def infer_app_label(module_name: str) -> str:
    app_config = django_apps.get_containing_app_config(module_name)
    if app_config is not None:
        return str(app_config.label)
    return module_name.split(".", 1)[0]


def register_task(task: Any) -> Any:
    return registry.register(task)


def get_task(name: str) -> Any:
    return registry.get_task(name)


def iter_tasks() -> Iterator[Any]:
    return registry.iter_tasks()


def is_registered(name: str) -> bool:
    return registry.is_registered(name)


def autodiscover_tasks() -> None:
    registry.autodiscover()


def clear_registry() -> None:
    registry.clear()
