"""Prepared submission handle for the public Python client."""

from __future__ import annotations

from dataclasses import dataclass

from weft.commands.types import PreparedSubmissionRequest

from ._task import Task
from ._types import SubmissionClientHandle


@dataclass(frozen=True, slots=True)
class PreparedSubmission:
    """Validated submission that has not yet written to the spawn queue."""

    client: SubmissionClientHandle
    _request: PreparedSubmissionRequest

    @property
    def name(self) -> str:
        return self._request.name

    def submit(self) -> Task:
        return self.client._submit_prepared(self._request)
