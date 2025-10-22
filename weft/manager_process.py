"""Entry point that runs a Manager in a standalone interpreter."""

from __future__ import annotations

import base64
import json
import sys

from weft.core.launcher import _task_process_entry


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 5:
        sys.stderr.write(
            "manager_process requires 5 arguments: task_cls_path, db_path, "
            "spec_b64, config_b64, poll_interval\n"
        )
        return 2

    task_cls_path, db_path, spec_b64, config_b64, poll_interval_s = args

    try:
        spec_json = base64.b64decode(spec_b64).decode("utf-8")
        config_json = base64.b64decode(config_b64).decode("utf-8")
        config = json.loads(config_json)
        poll_interval = float(poll_interval_s)
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"Invalid manager arguments: {exc}\n")
        return 2

    _task_process_entry(task_cls_path, db_path, spec_json, config, poll_interval)
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked via subprocess
    sys.exit(main())
