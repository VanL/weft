"""Weft configuration custody across a fresh task process.

Spec: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4].
"""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, cast

import pytest

from simplebroker import Config, ConfigField, resolve_config, serialize_config
from simplebroker.ext import InvalidConfigError
from weft._constants import WEFT_CONFIG_DEFAULTS, load_config, resolve_runtime_config
from weft.context import build_context
from weft.core.launcher import launch_task_process
from weft.core.taskspec import TaskSpec, validate_taskspec_payload
from weft.helpers import is_debug_enabled, is_logging_enabled

pytestmark = [pytest.mark.shared]


class ConfigSnapshotProbeTask:
    """Observe the real launcher's config handoff without owning broker resources."""

    def __init__(
        self,
        _db_path: object,
        taskspec: TaskSpec,
        *,
        config: Config,
    ) -> None:
        assert isinstance(config, Config)
        self.taskspec = taskspec
        self.config = config

    def run_until_stopped(self, *, poll_interval: float) -> None:
        del poll_interval
        observation: dict[str, Any] = {
            "pid": os.getpid(),
            "prefix": self.config.prefix,
            "cache_mb": self.config["CACHE_MB"],
            "batch_size": self.config["TASK_MONITOR_BATCH_SIZE"],
            "max_interval": self.config["MAX_INTERVAL"],
            "custom_value": self.config["TRANSPORT_PROBE_VALUE"],
            "context_root": str(
                build_context(config=self.config, create_database=False).root
            ),
            "child_environment": os.environ["WEFT_TASK_MONITOR_BATCH_SIZE"],
            "helper_debug": is_debug_enabled(),
            "helper_logging": is_logging_enabled(),
        }
        try:
            # Intentional invalid operation proves the received snapshot is read-only.
            cast(MutableMapping[str, Any], self.config)["CACHE_MB"] = 999
        except TypeError:
            observation["immutable"] = True
        else:
            observation["immutable"] = False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            try:
                resolve_config(
                    config=self.config,
                    override={"WEFT_TASK_MONITOR_BATCH_SIZE": 0},
                )
            except InvalidConfigError as exc:
                observation["local_validator_key"] = exc.key
            else:
                observation["local_validator_key"] = None
        output = Path(str(self.taskspec.spec.args[0]))
        output.write_text(json.dumps(observation), encoding="utf-8")


def test_fresh_spawn_preserves_config_snapshot_and_weft_validators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent values and an inherited default survive different child env values."""
    monkeypatch.delenv("WEFT_MAX_INTERVAL", raising=False)
    loaded = load_config(
        {
            "WEFT_CONTEXT": str(tmp_path / "parent-context"),
            "WEFT_CACHE_MB": 17,
            "WEFT_TASK_MONITOR_BATCH_SIZE": 37,
            "WEFT_DEBUG": True,
            "WEFT_LOGGING_ENABLED": False,
        }
    )
    # A sender-local validator cannot be pickled. The launcher must send values
    # as JSON; the child supplies Weft's own declarations instead of executable code.
    config = resolve_config(
        "WEFT",
        defaults={
            **WEFT_CONFIG_DEFAULTS,
            "TRANSPORT_PROBE_VALUE": ConfigField(
                "sender-resolved", "a string", lambda value: str(value)
            ),
        },
        override={f"WEFT_{key}": value for key, value in loaded.items()},
    )
    default_interval = config["MAX_INTERVAL"]
    monkeypatch.setenv("WEFT_CONTEXT", str(tmp_path / "wrong-child-context"))
    monkeypatch.setenv("WEFT_CACHE_MB", "invalid-child-ambient")
    monkeypatch.setenv("WEFT_TASK_MONITOR_BATCH_SIZE", "invalid-child-ambient")
    monkeypatch.setenv("WEFT_DEBUG", "0")
    monkeypatch.setenv("WEFT_LOGGING_ENABLED", "1")
    monkeypatch.setenv("WEFT_MAX_INTERVAL", str(default_interval + 1.0))
    output = tmp_path / "child-config.json"
    spec = validate_taskspec_payload(
        {
            "tid": "1777000000000000123",
            "name": "config-snapshot-probe",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
                "args": [str(output)],
            },
        }
    )
    process = launch_task_process(
        ConfigSnapshotProbeTask,
        str(tmp_path / "unused.db"),
        spec,
        config=config,
        detach_stdio=False,
    )
    try:
        process.join(timeout=20.0)
        assert not process.is_alive(), "config probe child did not exit"
        assert process.exitcode == 0
        observation = json.loads(output.read_text(encoding="utf-8"))
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)
        assert not process.is_alive(), "config probe child survived cleanup"
        process.close()

    assert observation == {
        "pid": observation["pid"],
        "prefix": "WEFT",
        "cache_mb": 17,
        "batch_size": 37,
        "max_interval": default_interval,
        "custom_value": "sender-resolved",
        "context_root": str((tmp_path / "parent-context").resolve()),
        "child_environment": "invalid-child-ambient",
        "helper_debug": True,
        "helper_logging": False,
        "immutable": True,
        "local_validator_key": "WEFT_TASK_MONITOR_BATCH_SIZE",
    }
    assert observation["pid"] != os.getpid()
    assert config["CACHE_MB"] == 17


@pytest.mark.parametrize("include_context", [True, False])
def test_context_json_snapshot_uses_local_declaration_without_ambient_reload(
    include_context: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both current and older Config snapshots keep their own root selection."""
    fields = dict(WEFT_CONFIG_DEFAULTS)
    overrides = {}
    if include_context:
        overrides["WEFT_CONTEXT"] = str(tmp_path / "captured")
    else:
        fields.pop("CONTEXT", None)
    original = resolve_config("WEFT", defaults=fields, override=overrides)
    monkeypatch.setenv("WEFT_CONTEXT", str(tmp_path / "ambient"))
    monkeypatch.setenv("WEFT_CACHE_MB", "invalid-ambient")

    restored = resolve_runtime_config(serialize_config(original))

    assert restored.get("CONTEXT") == (
        str(tmp_path / "captured") if include_context else None
    )
    with (
        pytest.warns(UserWarning, match="WEFT_CONTEXT"),
        pytest.raises(InvalidConfigError, match="WEFT_CONTEXT"),
    ):
        resolve_config(config=restored, override={"WEFT_CONTEXT": 9})
