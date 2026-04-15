"""Small import-ref helpers shared across runtime-facing modules.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3]
- docs/specifications/13-Agent_Runtime.md [AR-5]
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast


def import_ref(ref: str, *, bundle_root: str | Path | None = None) -> Any:
    """Resolve a ``module:name`` reference to a Python object."""
    module_name, object_name = split_import_ref(ref)
    module = import_module_ref(module_name, bundle_root=bundle_root)
    return getattr(module, object_name)


def import_callable_ref(
    ref: str,
    *,
    bundle_root: str | Path | None = None,
) -> Callable[..., Any]:
    """Resolve a ``module:name`` reference to a callable object."""
    resolved = import_ref(ref, bundle_root=bundle_root)
    if not callable(resolved):
        raise TypeError(f"Resolved reference is not callable: {ref}")
    return cast(Callable[..., Any], resolved)


def import_module_ref(
    module_name: str,
    *,
    bundle_root: str | Path | None = None,
) -> types.ModuleType:
    """Import *module_name*, preferring a bundle-local module when present."""
    normalized_bundle_root = _normalize_bundle_root(bundle_root)
    if normalized_bundle_root is not None:
        bundle_module = _import_bundle_module(
            module_name,
            bundle_root=normalized_bundle_root,
        )
        if bundle_module is not None:
            return bundle_module
    return importlib.import_module(module_name)


def split_import_ref(ref: str) -> tuple[str, str]:
    """Split and validate a ``module:name`` reference."""
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("Import reference must be a non-empty string")
    try:
        module_name, object_name = ref.rsplit(":", 1)
    except ValueError as exc:
        raise ValueError(
            f"Invalid import reference {ref!r}; expected 'module:name'"
        ) from exc
    module_name = module_name.strip()
    object_name = object_name.strip()
    if not module_name or not object_name:
        raise ValueError(f"Invalid import reference {ref!r}; expected 'module:name'")
    return module_name, object_name


def _import_bundle_module(
    module_name: str,
    *,
    bundle_root: Path,
) -> types.ModuleType | None:
    if not _bundle_module_exists(bundle_root, module_name):
        return None
    synthetic_root = _ensure_bundle_root_package(bundle_root)
    return importlib.import_module(f"{synthetic_root}.{module_name}")


def _bundle_module_exists(bundle_root: Path, module_name: str) -> bool:
    module_path = bundle_root.joinpath(*module_name.split("."))
    return (
        module_path.with_suffix(".py").is_file()
        or (module_path / "__init__.py").is_file()
    )


def _ensure_bundle_root_package(bundle_root: Path) -> str:
    synthetic_root = f"_weft_bundle_{_bundle_root_digest(bundle_root)}"
    if synthetic_root in sys.modules:
        return synthetic_root

    module = types.ModuleType(synthetic_root)
    module.__file__ = str(bundle_root)
    module.__package__ = synthetic_root
    module.__path__ = [str(bundle_root)]
    spec = importlib.util.spec_from_loader(synthetic_root, loader=None, is_package=True)
    if spec is not None:
        spec.submodule_search_locations = [str(bundle_root)]
    module.__spec__ = spec
    sys.modules[synthetic_root] = module
    return synthetic_root


def _bundle_root_digest(bundle_root: Path) -> str:
    return hashlib.sha256(str(bundle_root).encode("utf-8")).hexdigest()[:16]


def _normalize_bundle_root(bundle_root: str | Path | None) -> Path | None:
    if bundle_root is None:
        return None
    return Path(bundle_root).expanduser().resolve()


__all__ = [
    "import_callable_ref",
    "import_module_ref",
    "import_ref",
    "split_import_ref",
]
