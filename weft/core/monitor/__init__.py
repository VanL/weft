"""TaskMonitor support package.

This package owns the TaskMonitor runtime, cleanup orchestration, task-log
scanning, durable collation store, and SQL helpers.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.3]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
"""

from __future__ import annotations
