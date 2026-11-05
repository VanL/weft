"""Pure shared dispatch-observation conversion [MF-5], [CLI-1.2.1], [OBS.6].

The Manager owns facts and publication. Readers validate a captured complete
snapshot; conversion performs no broker operations and grants no authority.

Spec references:
- docs/specifications/03-Manager_Architecture.md [MA-1.8]
- docs/specifications/05-Message_Flow_and_State.md [MF-5]
- docs/specifications/07-System_Invariants.md [OBS.6]
- docs/specifications/10-CLI_Interface.md [CLI-1.2.1]
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from simplebroker import format_message_id
from weft._constants import DISPATCH_REFUSAL_DESCRIPTORS


def refusal_item(reason: str, *, scope: str, lane: str, **facts: Any) -> dict[str, Any]:
    """Derive immutable descriptors from the single reason inventory [MF-5]."""
    barrier, ending = DISPATCH_REFUSAL_DESCRIPTORS[reason]
    return dict(
        reason=reason,
        scope=scope,
        lane=lane,
        barrier=barrier,
        ends_when=ending,
        **facts,
    )


def refusal_semantics(observation: Mapping[str, Any]) -> tuple[Any, ...]:
    """Ignore lane witness/count/usage churn when deciding publication edges."""
    return (
        observation["assessment"],
        tuple(
            (
                item["reason"],
                item["scope"],
                item["lane"],
                tuple(item.get("subjects", ())),
                tuple(
                    (entry["queue"], entry["message_id"])
                    for entry in item.get("evidence", ())
                ),
            )
            for item in observation["items"]
        ),
    )


def _valid_lane(item: Mapping[str, Any]) -> bool:
    """Validate nullable lane observations before renderers index their fields."""
    if not item.get("queue") or item.get("count_basis") != "manager_indexed":
        return False
    if not isinstance(item["queue"], str) or "measurements" not in item:
        return False
    if item["measurements"] is not None and not isinstance(item["measurements"], dict):
        return False
    return all(
        field in item
        and (
            item[field] is None or (type(item[field]) is int and item[field] >= minimum)
        )
        for field, minimum in (("known_pending_count", 0), ("oldest_request", 1))
    )


def _valid_cause(item: Mapping[str, Any]) -> bool:
    subjects, evidence = item.get("subjects"), item.get("evidence")
    if not isinstance(subjects, list) or not all(
        isinstance(tid, str) and tid.isdecimal() for tid in subjects
    ):
        return False
    if not isinstance(evidence, list):
        return False
    return all(
        isinstance(entry, dict)
        and isinstance(entry.get("queue"), str)
        and bool(entry["queue"])
        and "message_id" in entry
        and (
            entry["message_id"] is None
            or (type(entry["message_id"]) is int and entry["message_id"] > 0)
        )
        for entry in evidence
    )


def dispatch_refusals_observation(value: Any) -> dict[str, Any]:
    """Reject incomplete reader observations rather than report an open gate."""
    unavailable: dict[str, Any] = {"assessment": "unavailable", "items": []}
    if not isinstance(value, Mapping) or not isinstance(value.get("assessment"), str):
        return unavailable
    if value["assessment"] not in {"known", "unavailable"} or not isinstance(
        value.get("items"), list
    ):
        return unavailable
    for item in value["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("reason"), str):
            return unavailable
        expected = DISPATCH_REFUSAL_DESCRIPTORS.get(item["reason"])
        if (
            expected is None
            or (item.get("barrier"), item.get("ends_when")) != expected
            or item.get("lane") not in ("public", "internal")
        ):
            return unavailable
        valid = (
            _valid_lane(item)
            if item.get("scope") == "dispatch_lane"
            else _valid_cause(item)
            if item.get("scope") in ("request", "family_path")
            else False
        )
        if not valid:
            return unavailable
    return copy.deepcopy(dict(value))


def dispatch_refusals_json(value: Any) -> Any:
    """Keep broker IDs lossless externally without converting counts [SB-0.2]."""
    if isinstance(value, dict):
        return {
            key: format_message_id(item)
            if key in {"message_id", "oldest_request"} and type(item) is int
            else dispatch_refusals_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [dispatch_refusals_json(item) for item in value]
    return value


def dispatch_refusal_lines(
    observation: Mapping[str, Any], source: Mapping[str, Any] | None
) -> list[str]:
    """Bound human samples while retaining all causal references in JSON."""
    location = f"{source['queue']}@{source['message_id']}" if source else "unavailable"
    lines = [f"Dispatch refusals: {observation['assessment']} (snapshot {location})"]
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in observation["items"]:
        key = (item["reason"], item["scope"], item["lane"])
        if item["scope"] == "dispatch_lane":
            groups[key] = item
        else:
            group = groups.setdefault(key, {**item, "subjects": [], "evidence": []})
            group["subjects"].extend(item["subjects"])
            group["evidence"].extend(item["evidence"])
    for item in groups.values():
        text = f"  {item['reason']} scope={item['scope']} lane={item['lane']} ends_when={item['ends_when']}"
        if item["scope"] == "dispatch_lane":
            text += f" count={item['known_pending_count']} basis={item['count_basis']} oldest_request={item['oldest_request']} measurements={item['measurements']} (last observed)"
        else:
            subjects, evidence = item["subjects"], item["evidence"]
            text += f" subjects={subjects[:3]} ({len(subjects)} total) evidence={evidence[:3]} ({len(evidence)} total); --json for all references"
        lines.append(text)
    return lines
