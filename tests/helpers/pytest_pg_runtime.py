"""Reuse the canonical physical PostgreSQL test lifecycle.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest


def load_pytest_pg_module() -> ModuleType:
    """Load the existing script without invoking its pytest entry point."""
    script_path = Path(__file__).resolve().parents[2] / "bin" / "pytest-pg"
    loader = importlib.machinery.SourceFileLoader(
        "weft_pytest_pg_script", str(script_path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError(f"Unable to load pytest-pg script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


@pytest.fixture
def isolated_postgres_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Own physical capacity while all normal pytest workers remain concurrent.

    Per-schema isolation cannot bound unrelated workers' server-wide sessions.
    Backend configuration is local to this pytest worker and unwinds after all
    test-owned harnesses, tasks, observers and pressure processes have closed.
    """
    runtime = load_pytest_pg_module()
    name, dsn = runtime._start_postgres_container()
    try:
        env = runtime._build_test_env(dsn=dsn)
        for key in (
            *runtime.BACKEND_ENV_KEYS,
            "WEFT_PG_TEST_DSN",
            "BROKER_TEST_BACKEND",
        ):
            if key in env:
                monkeypatch.setenv(key, env[key])
            else:
                monkeypatch.delenv(key, raising=False)
        runtime._initialize_broker_schema(env=env)
        yield
    except BaseException:
        runtime._print_container_logs(name)
        raise
    finally:
        runtime._cleanup_container(name)
