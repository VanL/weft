"""Entry point that runs a Manager in a standalone interpreter.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-7]
"""

from __future__ import annotations

import base64
import json
import sys
from typing import Any

from simplebroker import BrokerTarget, deserialize_broker_target
from weft.core.launcher import _task_process_entry
from weft.core.taskspec import TaskSpec


def run_manager_process(
    task_cls_path: str,
    broker_target: BrokerTarget | str,
    spec: TaskSpec,
    config: dict[str, Any] | None,
    poll_interval: float,
) -> None:
    """Run a manager through the shared task-process entry path."""

    _task_process_entry(
        task_cls_path,
        broker_target,
        spec.model_dump_json(),
        config,
        poll_interval,
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 5:
        sys.stderr.write(
            "manager_process requires 5 arguments: task_cls_path, broker_target_b64, "
            "spec_b64, config_b64, poll_interval\n"
        )
        return 2

    task_cls_path, broker_target_b64, spec_b64, config_b64, poll_interval_s = args

    try:
        broker_target_json = base64.b64decode(broker_target_b64).decode("utf-8")
        broker_target = deserialize_broker_target(broker_target_json)
        spec_json = base64.b64decode(spec_b64).decode("utf-8")
        config_json = base64.b64decode(config_b64).decode("utf-8")
        config = json.loads(config_json)
        poll_interval = float(poll_interval_s)
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"Invalid manager arguments: {exc}\n")
        return 2

    try:
        spec = TaskSpec.model_validate_json(spec_json)
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"Invalid manager TaskSpec: {exc}\n")
        return 2

    run_manager_process(
        task_cls_path,
        broker_target,
        spec,
        config,
        poll_interval,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked via subprocess
    sys.exit(main())
