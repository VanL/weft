"""Django settings helpers for the Weft integration."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

DEFAULT_AUTODISCOVER_MODULE = "weft_tasks"
DEFAULT_REALTIME_TRANSPORT = "sse"
DEFAULT_TEST_MODE = "broker"


def _import_ref(ref: str) -> Any:
    module_name, sep, object_name = ref.partition(":")
    if not sep or not module_name or not object_name:
        raise ImproperlyConfigured("Import refs must use 'module:object' format")
    module = import_module(module_name)
    try:
        return getattr(module, object_name)
    except AttributeError as exc:
        raise ImproperlyConfigured(
            f"Could not resolve '{object_name}' from module '{module_name}'"
        ) from exc


def _settings_dict() -> dict[str, Any]:
    value = getattr(settings, "WEFT_DJANGO", {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ImproperlyConfigured("WEFT_DJANGO must be a dictionary when set")
    return dict(value)


def get_autodiscover_module() -> str:
    settings_dict = _settings_dict()
    module_name = settings_dict.get("AUTODISCOVER_MODULE", DEFAULT_AUTODISCOVER_MODULE)
    if not isinstance(module_name, str) or not module_name.strip():
        raise ImproperlyConfigured(
            "WEFT_DJANGO['AUTODISCOVER_MODULE'] must be a non-empty string"
        )
    return module_name


def get_default_task_settings() -> dict[str, Any]:
    settings_dict = _settings_dict()
    defaults = settings_dict.get("DEFAULT_TASK", {})
    if defaults is None:
        return {}
    if not isinstance(defaults, dict):
        raise ImproperlyConfigured("WEFT_DJANGO['DEFAULT_TASK'] must be a dict")
    return dict(defaults)


def _has_weft_environment_override() -> bool:
    return any(key.startswith("WEFT_") for key in os.environ)


def resolve_context_override() -> str | Path | None:
    settings_dict = _settings_dict()
    explicit = settings_dict.get("CONTEXT")
    if explicit:
        if isinstance(explicit, Path):
            return explicit
        return str(explicit)
    if _has_weft_environment_override():
        return None
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        return None
    return str(base_dir)


def get_realtime_transport() -> str:
    realtime = _settings_dict().get("REALTIME", {})
    if realtime is None:
        return DEFAULT_REALTIME_TRANSPORT
    if not isinstance(realtime, dict):
        raise ImproperlyConfigured("WEFT_DJANGO['REALTIME'] must be a dict")
    transport = realtime.get("TRANSPORT", DEFAULT_REALTIME_TRANSPORT)
    if not isinstance(transport, str) or not transport.strip():
        raise ImproperlyConfigured(
            "WEFT_DJANGO['REALTIME']['TRANSPORT'] must be a string"
        )
    return transport


def get_test_mode() -> str:
    test_mode = _settings_dict().get("TEST_MODE", DEFAULT_TEST_MODE)
    if not isinstance(test_mode, str) or not test_mode.strip():
        raise ImproperlyConfigured("WEFT_DJANGO['TEST_MODE'] must be a string")
    return test_mode


def _import_callable(setting_name: str) -> Callable[..., Any] | None:
    value = _settings_dict().get(setting_name)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ImproperlyConfigured(f"WEFT_DJANGO['{setting_name}'] must be a string")
    if ":" in value:
        imported = _import_ref(value)
    else:
        imported = import_string(value)
    if not callable(imported):
        raise ImproperlyConfigured(
            f"WEFT_DJANGO['{setting_name}'] must resolve to a callable"
        )
    return cast(Callable[..., Any], imported)


def get_request_id_provider() -> Callable[[], str | None] | None:
    provider = _import_callable("REQUEST_ID_PROVIDER")
    return cast(Callable[[], str | None] | None, provider)


def get_authz_callable(
    *,
    required: bool = False,
) -> Callable[[Any, str, str], bool] | None:
    authz = _import_callable("AUTHZ")
    if authz is None and required:
        raise ImproperlyConfigured(
            "Including weft_django.urls requires WEFT_DJANGO['AUTHZ']"
        )
    return cast(Callable[[Any, str, str], bool] | None, authz)


def merge_metadata(*metadata_maps: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for metadata in metadata_maps:
        if not isinstance(metadata, Mapping):
            continue
        merged.update(metadata)
    return merged
