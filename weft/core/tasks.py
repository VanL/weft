"""Task implementation for Weft.

This module provides the Task class which extends simplebroker's BaseWatcher
to create a task execution system.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from simplebroker.sbqueue import Queue
from simplebroker.watcher import BaseWatcher
from weft._constants import CONTROL_STOP, STATUS_RUNNING
from weft.helpers import debug_print, format_tid, log_debug, log_error, log_info

from .taskspec import TaskSpec

if TYPE_CHECKING:
    from simplebroker.db import BrokerDB


class Task(BaseWatcher):
    """Task executor that processes messages from multiple queues.

    The Task class extends BaseWatcher to provide task execution capabilities
    for the Weft workflow system. It can watch multiple input queues and
    responds to control commands.
    """

    def __init__(
        self,
        db: BrokerDB | str | Path,
        taskspec: TaskSpec,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Initialize a Task.

        Args:
            db: Database instance or path
            taskspec: Task specification containing queue definitions (REQUIRED)
            stop_event: Optional event to signal task shutdown

        Raises:
            ValueError: If the TaskSpec doesn't meet strict validation requirements
        """
        super().__init__(db, stop_event)

        # Validate the provided TaskSpec meets all requirements
        taskspec._validate_strict_requirements()

        self.taskspec = taskspec
        self.tid = taskspec.tid

        # Track if we should stop
        self.should_stop = False

        # Store the database path for Queue instances
        if isinstance(db, str):
            self.db_path = db
        elif isinstance(db, Path):
            self.db_path = str(db)
        else:
            # BrokerDB instance
            self.db_path = str(db.db_path)

    def _drain_queue(self) -> None:
        """Process messages from all monitored queues.

        This method polls all input queues and the control queue,
        processing messages as they arrive. It stops when a STOP
        command is received on ctrl_in.
        """
        # Check control queue first
        self._check_control_queue()

        # If we should stop, exit
        if self.should_stop:
            return

        # Check all input queues
        for _queue_name, queue_path in self.taskspec.io.inputs.items():
            self._process_input_queue(queue_path)

    def _check_control_queue(self) -> None:
        """Check the control queue for commands."""
        try:
            ctrl_in = self.taskspec.io.control.get("ctrl_in")
            if not ctrl_in:
                return

            # Create Queue instance for control queue
            ctrl_queue = Queue(ctrl_in, db_path=self.db_path)

            # Read from control queue
            message = ctrl_queue.read()

            if message:
                log_debug(
                    f"Task {format_tid(self.tid)} received control message: {message}"
                )

                # Check for STOP command
                if message.strip().upper() == CONTROL_STOP:
                    log_info(f"Task {format_tid(self.tid)} received STOP command")
                    self.should_stop = True
                    if self._stop_event:
                        self._stop_event.set()
                else:
                    # Handle other control commands in the future
                    log_debug(
                        f"Task {format_tid(self.tid)} ignoring unknown control command: {message}"
                    )

        except Exception as e:
            log_error(f"Error checking control queue: {e}")

    def _process_input_queue(self, queue_path: str) -> None:
        """Process messages from an input queue."""
        try:
            # Create Queue instance for input queue
            input_queue = Queue(queue_path, db_path=self.db_path)

            # Read from input queue
            message = input_queue.read()

            if message:
                log_debug(
                    f"Task {format_tid(self.tid)} received input message from {queue_path}: {message}"
                )
                debug_print(f"[{format_tid(self.tid)}] Processing: {message[:100]}...")
                # TODO: Process the message based on task type

        except Exception as e:
            log_error(f"Error processing input queue {queue_path}: {e}")

    def run_forever(self) -> None:
        """Run the task until stopped via control command."""
        log_info(f"Task {format_tid(self.tid)} starting")
        debug_print(
            f"Task {format_tid(self.tid)} configuration:",
            self.taskspec.model_dump_json(indent=2),
        )

        # Update status to running using convenience method
        self.taskspec.mark_running()

        while not self.should_stop and not (
            self._stop_event and self._stop_event.is_set()
        ):
            self._drain_queue()

            # Small sleep to prevent busy waiting
            if not self.should_stop:
                time.sleep(0.01)

        # Update status when stopping using convenience method
        if self.taskspec.state.status == STATUS_RUNNING:
            self.taskspec.mark_completed()

        # Cleanup
        self._cleanup()
        log_info(f"Task {format_tid(self.tid)} stopped")
        debug_print(
            f"Task {format_tid(self.tid)} final status: {self.taskspec.state.status}"
        )

    def _cleanup(self) -> None:
        """Clean up task resources."""
        # No need to close Queue instances as they manage their own connections
        pass

    def to_json(self) -> str:
        """Serialize the TaskSpec to JSON."""
        return self.taskspec.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, json_str: str, db: BrokerDB | str | Path) -> Task:
        """Create a Task from JSON TaskSpec.

        The JSON TaskSpec must already have all required fields and defaults applied.
        """
        taskspec = TaskSpec.model_validate_json(json_str)
        # The taskspec from JSON should already have defaults applied
        # The Task constructor will validate strict requirements
        return cls(db=db, taskspec=taskspec)
