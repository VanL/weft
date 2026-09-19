"""Entry point that runs a Manager in a standalone interpreter.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-3]
- docs/specifications/05-Message_Flow_and_State.md [MF-7]
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
"""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Mapping
from typing import Any

from simplebroker import BrokerTarget, deserialize_broker_target, serialize_config
from weft._constants import TASK_POLL_INTERVAL_NONE_TOKEN, resolve_runtime_config
from weft.core.launcher import _task_process_entry
from weft.core.taskspec import (
    TaskSpec,
    decode_taskspec_transport_payload,
    encode_taskspec_transport_payload,
)


def run_manager_process(
    task_cls_path: str,
    broker_target: BrokerTarget | str,
    spec: TaskSpec,
    config: Mapping[str, Any] | None,
    poll_interval: float | None,
    *,
    hard_exit_on_return: bool = False,
) -> None:
    """Run a manager through the shared task-process entry path."""

    _task_process_entry(
        task_cls_path,
        broker_target,
        json.dumps(encode_taskspec_transport_payload(spec)),
        serialize_config(resolve_runtime_config(config)),
        poll_interval,
        hard_exit_on_return,
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
        poll_interval = (
            None
            if poll_interval_s == TASK_POLL_INTERVAL_NONE_TOKEN
            else float(poll_interval_s)
        )
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Invalid manager arguments: {exc}\n")
        return 2

    try:
        config = resolve_runtime_config(config_json)
    except (TypeError, ValueError) as exc:
        sys.stderr.write(f"Invalid manager config: {exc}\n")
        return 2

    try:
        spec_payload = json.loads(spec_json)
        if not isinstance(spec_payload, dict):
            raise TypeError("manager TaskSpec JSON root must be an object")
        spec = decode_taskspec_transport_payload(spec_payload)
    except (TypeError, ValueError) as exc:
        sys.stderr.write(f"Invalid manager TaskSpec: {exc}\n")
        return 2

    run_manager_process(
        task_cls_path,
        broker_target,
        spec,
        config,
        poll_interval,
        hard_exit_on_return=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked via subprocess
    sys.exit(main())
