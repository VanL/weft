"""Shared exact-message prune deletion helper.

All pruning surfaces must delete through this module so queue persistence,
exact message IDs, and idempotent missing-row behavior stay consistent.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Protocol

from simplebroker.ext import BrokerError
from weft.context import WeftContext


class ExactPruneCandidate(Protocol):
    """Minimal protocol for candidates deletable by exact queue/message ID."""

    @property
    def queue(self) -> str:
        """Queue containing the exact row."""
        ...

    @property
    def message_id(self) -> int:
        """Exact broker message ID selected for deletion."""
        ...

    @property
    def report_only(self) -> bool:
        """Whether ordinary apply should leave the candidate untouched."""
        ...


def apply_exact_prune_candidates[
    Candidate: ExactPruneCandidate,
    AppliedCandidate,
](
    ctx: WeftContext,
    candidates: Iterable[Candidate],
    *,
    apply_result: Callable[[Candidate, bool, str | None], AppliedCandidate],
    force: bool = False,
    exact_status: bool = False,
    reconcile_missing: bool = False,
) -> list[AppliedCandidate]:
    """Delete exact prune candidates and return caller-shaped apply results.

    Args:
        ctx: Weft context owning broker access.
        candidates: Exact prune candidates selected by the canonical scanner.
        apply_result: Adapter that returns the caller's result candidate shape.
        force: Whether report-only candidates should be deleted.
        exact_status: Delete one ID at a time so mixed present/missing rows
            return per-candidate status. Use this when reconciliation must not
            let one stale ID block deletion of present IDs in the same queue.
        reconcile_missing: Treat rows verified missing as already deleted.
            When the batch delete reports fewer deletions than requested,
            every batched candidate is re-verified with a per-ID exact
            delete: ``True`` means the row was physically deleted now,
            ``False`` means the backend verified the row is absent
            (idempotent-complete), and an exception is reported as that
            candidate's error. A row that is still present can therefore
            never be reported ``deleted=True``. Use this only for
            idempotent cleanup paths where missing rows mean the work is
            already complete.

    Returns:
        Per-candidate apply results in queue-grouped processing order.

    Spec: [OBS.13], [OBS.16], [OBS.17]
    """

    by_queue: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_queue[candidate.queue].append(candidate)

    applied: list[AppliedCandidate] = []
    for queue_name, queue_candidates in by_queue.items():
        queue = ctx.queue(queue_name, persistent=_queue_is_persistent(queue_name))
        try:
            queue_results: list[AppliedCandidate | None] = [None] * len(
                queue_candidates
            )
            deletable: list[tuple[int, Candidate]] = []
            for index, candidate in enumerate(queue_candidates):
                if candidate.report_only and not force:
                    queue_results[index] = apply_result(candidate, False, None)
                    continue
                deletable.append((index, candidate))

            if deletable:
                if exact_status:
                    for index, candidate in deletable:
                        try:
                            deleted = queue.delete(message_id=candidate.message_id)
                        except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                            queue_results[index] = apply_result(
                                candidate,
                                False,
                                str(exc),
                            )
                        else:
                            queue_results[index] = apply_result(
                                candidate,
                                deleted,
                                None,
                            )
                else:
                    try:
                        deleted_count = queue.delete_many(
                            [candidate.message_id for _index, candidate in deletable]
                        )
                    except (BrokerError, OSError, RuntimeError, ValueError) as exc:
                        for index, candidate in deletable:
                            queue_results[index] = apply_result(
                                candidate,
                                False,
                                str(exc),
                            )
                    else:
                        if deleted_count == len(deletable):
                            for index, candidate in deletable:
                                queue_results[index] = apply_result(
                                    candidate,
                                    True,
                                    None,
                                )
                        elif reconcile_missing:
                            # The batch under-deleted, so per-candidate
                            # status is unknown. Re-verify each candidate
                            # with a per-ID exact delete: True deletes the
                            # row now, False proves it is already absent.
                            # Either way the row is verifiably gone, so a
                            # present row can never be reported deleted.
                            for index, candidate in deletable:
                                try:
                                    queue.delete(message_id=candidate.message_id)
                                except (
                                    BrokerError,
                                    OSError,
                                    RuntimeError,
                                    ValueError,
                                ) as exc:
                                    queue_results[index] = apply_result(
                                        candidate,
                                        False,
                                        str(exc),
                                    )
                                else:
                                    queue_results[index] = apply_result(
                                        candidate,
                                        True,
                                        None,
                                    )
                        elif deleted_count == 0:
                            for index, candidate in deletable:
                                queue_results[index] = apply_result(
                                    candidate,
                                    False,
                                    None,
                                )
                        else:
                            error = (
                                "batch delete removed "
                                f"{deleted_count} of {len(deletable)} exact rows; "
                                "per-row status unavailable"
                            )
                            for index, candidate in deletable:
                                queue_results[index] = apply_result(
                                    candidate,
                                    False,
                                    error,
                                )

            for result in queue_results:
                if result is not None:
                    applied.append(result)
        finally:
            queue.close()
    return applied


def _queue_is_persistent(queue_name: str) -> bool:
    """Return whether the prune helper must open a persistent queue handle."""

    return queue_name.endswith(".outbox")
