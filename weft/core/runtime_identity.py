"""Process-local task identity and system-owned launch evidence.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5], [MF-6]
- docs/specifications/07-System_Invariants.md [OBS.6]
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from simplebroker import (
    BrokerTarget,
    Config,
    deserialize_broker_target,
    serialize_broker_target,
    serialize_config,
)
from weft._constants import resolve_runtime_config
from weft.core.taskspec import TaskSpec
from weft.helpers.message_ids import normalize_exact_message_id


def _validate_runtime_tid(tid: str) -> None:
    if not isinstance(tid, str) or normalize_exact_message_id(tid) == 0:
        raise ValueError("runtime TID must be a nonzero full TID")


def _validate_ancestry(ancestry: tuple[str, ...]) -> None:
    for ancestor in ancestry:
        _validate_runtime_tid(ancestor)
    if len(set(ancestry)) != len(ancestry):
        raise ValueError("runtime ancestry must be acyclic")


def _runtime_target_snapshot(target: BrokerTarget | str | None) -> BrokerTarget | None:
    """Detach resolved target data before application env/cwd changes [SB-0.4]."""
    if isinstance(target, str):
        target = BrokerTarget(backend_name="sqlite", target=target)
    if target is None:
        return None
    if target.target_path is not None:
        target = replace(target, target=str(target.target_path.resolve(strict=False)))
    return deserialize_broker_target(serialize_broker_target(target))


def canonical_runtime_context(target: BrokerTarget | str) -> str:
    """Return the existing credential-free resolved broker identity [MF-1]."""
    if isinstance(target, str):
        target = BrokerTarget(backend_name="sqlite", target=target)
    if target.target_path is not None:
        return f"{target.backend_name}:{target.target_path.resolve(strict=False)}"
    parsed = urlsplit(target.target)
    sanitized_target = target.target
    if parsed.scheme and parsed.netloc:
        hostname = parsed.hostname or ""
        username = parsed.username or ""
        hostinfo = f"{hostname}:{parsed.port}" if parsed.port is not None else hostname
        netloc = f"{username}@{hostinfo}" if username else hostinfo
        sanitized_target = urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )
    identity = {
        "backend": target.backend_name,
        "target": sanitized_target,
        "options": target.backend_options,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    return f"{target.backend_name}:{digest}"


def connection_owner_label(tid: str, context: str) -> str:
    """Build a compact non-secret label using the full TID and context [SB-0.4]."""
    _validate_runtime_tid(tid)
    digest = hashlib.sha256(context.encode("utf-8")).hexdigest()[:24]
    return f"weft-{digest}-{tid}"


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Identity installed once in a dedicated wrapper or host worker [MF-1]."""

    tid: str
    context: str
    backend_name: str
    root_tid: str | None = None
    ancestry_tids: tuple[str, ...] = ()
    scheduling_class: Literal["finite", "background", "internal"] = "finite"
    connection_owner: str | None = None
    broker_target: BrokerTarget | str | None = None
    context_root: str | None = None
    config: Config | None = None
    submitter_tid: str | None = None

    def __post_init__(self) -> None:
        _validate_runtime_tid(self.tid)
        object.__setattr__(
            self, "broker_target", _runtime_target_snapshot(self.broker_target)
        )
        if (
            not isinstance(self.context, str)
            or not isinstance(self.backend_name, str)
            or not self.context
            or not self.backend_name
        ):
            raise ValueError("runtime context and backend must be nonempty strings")
        if not self.context.startswith(f"{self.backend_name}:"):
            raise ValueError("runtime context does not match its backend")
        if (
            self.broker_target is not None
            and canonical_runtime_context(self.broker_target) != self.context
        ):
            raise ValueError("runtime broker target does not match its context")
        if self.submitter_tid is not None:
            _validate_runtime_tid(self.submitter_tid)
        root_tid = self.tid if self.root_tid is None else self.root_tid
        _validate_runtime_tid(root_tid)
        object.__setattr__(self, "root_tid", root_tid)
        if not isinstance(self.ancestry_tids, tuple):
            raise TypeError("runtime ancestry must be an immutable tuple")
        _validate_ancestry(self.ancestry_tids)
        if self.tid in self.ancestry_tids:
            raise ValueError("runtime ancestry must exclude the task itself")
        if self.submitter_tid is None and (root_tid != self.tid or self.ancestry_tids):
            raise ValueError("root runtime identity cannot have ancestors")
        if not isinstance(self.scheduling_class, str) or self.scheduling_class not in {
            "finite",
            "background",
            "internal",
        }:
            raise ValueError("unsupported runtime scheduling class")
        owner = connection_owner_label(self.tid, self.context)
        if self.connection_owner is not None and self.connection_owner != owner:
            raise ValueError("connection owner does not match runtime identity")
        object.__setattr__(self, "connection_owner", owner)

    def to_dict(self) -> dict[str, Any]:
        """Carry data, never a live broker resource, across spawn [SB-0.4]."""
        target = self.broker_target
        assert not isinstance(target, str)
        return {
            "tid": self.tid,
            "context": self.context,
            "backend_name": self.backend_name,
            "root_tid": self.root_tid,
            "ancestry_tids": list(self.ancestry_tids),
            "scheduling_class": self.scheduling_class,
            "connection_owner": self.connection_owner,
            "broker_target": serialize_broker_target(target)
            if target is not None
            else None,
            "context_root": self.context_root,
            "submitter_tid": self.submitter_tid,
            "config": serialize_config(self.config)
            if self.config is not None
            else None,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> RuntimeIdentity:
        """Restore the receiver's config declarations before broker use [SB-0.4]."""
        if set(payload) != {
            "tid",
            "context",
            "backend_name",
            "root_tid",
            "ancestry_tids",
            "scheduling_class",
            "connection_owner",
            "broker_target",
            "context_root",
            "submitter_tid",
            "config",
        }:
            raise ValueError("runtime identity has unsupported or missing fields")
        raw_target = payload.get("broker_target")
        if raw_target is not None and not isinstance(raw_target, str | Mapping):
            raise ValueError("runtime broker target must be data-only")
        target = (
            deserialize_broker_target(raw_target) if raw_target is not None else None
        )
        raw_ancestry = payload["ancestry_tids"]
        if not isinstance(raw_ancestry, list):
            raise TypeError("runtime ancestry transport must be a list")
        if payload["root_tid"] is None or payload["connection_owner"] is None:
            raise ValueError("runtime identity requires root and owner")
        raw_config = payload.get("config")
        return cls(
            tid=payload["tid"],
            context=payload["context"],
            backend_name=payload["backend_name"],
            root_tid=payload["root_tid"],
            ancestry_tids=tuple(raw_ancestry),
            scheduling_class=payload["scheduling_class"],
            connection_owner=payload["connection_owner"],
            broker_target=target,
            context_root=payload.get("context_root"),
            submitter_tid=payload.get("submitter_tid"),
            config=resolve_runtime_config(raw_config)
            if raw_config is not None
            else None,
        )


_runtime_identity: RuntimeIdentity | None = None


def get_runtime_identity() -> RuntimeIdentity | None:
    """Return the installed task identity, absent in ordinary callers [MF-1]."""
    return _runtime_identity


def install_runtime_identity(identity: RuntimeIdentity) -> None:
    """Bind attribution before this process opens any PostgreSQL resource [SB-0.4]."""
    global _runtime_identity
    if _runtime_identity is not None:
        if _runtime_identity != identity:
            raise RuntimeError("runtime identity is immutable for this process")
        return
    if identity.backend_name == "postgres":
        # Optional backend dependency: SQLite must never load PostgreSQL helpers.
        from simplebroker_pg import bind_connection_owner

        assert identity.connection_owner is not None
        bind_connection_owner(identity.connection_owner)
    _runtime_identity = identity


def submission_lineage(identity: RuntimeIdentity) -> dict[str, Any]:
    """Carry finite ancestry through transparent background/internal callers [MF-1]."""
    ancestry = identity.ancestry_tids
    if identity.scheduling_class == "finite":
        ancestry = (*ancestry, identity.tid)
    return {
        "tid": identity.tid,
        "context": identity.context,
        "root_tid": identity.root_tid,
        "ancestry_tids": list(ancestry),
    }


def validate_submission_lineage(
    payload: Mapping[str, Any], context: str
) -> tuple[str, str, tuple[str, ...]]:
    """Validate the single current same-context spawn-envelope contract [MF-1]."""
    if set(payload) != {"tid", "context", "root_tid", "ancestry_tids"}:
        raise ValueError("submission lineage has unsupported or missing fields")
    if payload["context"] != context:
        raise ValueError("submission lineage context does not match destination")
    _validate_runtime_tid(payload["tid"])
    _validate_runtime_tid(payload["root_tid"])
    ancestry = payload["ancestry_tids"]
    if not isinstance(ancestry, list):
        raise TypeError("submission ancestry must be a list")
    _validate_ancestry(tuple(ancestry))
    return payload["root_tid"], payload["tid"], tuple(ancestry)


def runtime_identity_for_task(
    spec: TaskSpec,
    target: BrokerTarget | str,
    *,
    submitted_by: Mapping[str, Any] | None = None,
    internal: bool = False,
    config: Config | None = None,
) -> RuntimeIdentity:
    """Derive launch identity from validated TaskSpec and trusted route [MF-1]."""
    assert spec.tid is not None
    context = canonical_runtime_context(target)
    root_tid = spec.tid
    submitter_tid: str | None = None
    ancestry: tuple[str, ...] = ()
    if submitted_by is not None:
        root_tid, submitter_tid, ancestry = validate_submission_lineage(
            submitted_by, context
        )
    scheduling_class: Literal["finite", "background", "internal"] = (
        "internal"
        if internal
        else "background"
        if spec.spec.persistent or spec.spec.interactive
        else "finite"
    )
    return RuntimeIdentity(
        tid=spec.tid,
        context=context,
        backend_name=target.backend_name
        if isinstance(target, BrokerTarget)
        else "sqlite",
        root_tid=root_tid,
        submitter_tid=submitter_tid,
        ancestry_tids=ancestry,
        scheduling_class=scheduling_class,
        broker_target=target,
        context_root=spec.spec.weft_context,
        config=config,
    )
