"""Process-local task identity and system-owned launch evidence.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
- docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5], [MF-6]
- docs/specifications/07-System_Invariants.md [OBS.6]
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from simplebroker import (
    BrokerTarget,
    Config,
    deserialize_broker_target,
    serialize_broker_target,
    serialize_config,
)
from weft._constants import resolve_runtime_config
from weft.helpers.message_ids import normalize_exact_message_id


def _validate_runtime_tid(tid: str) -> None:
    if not isinstance(tid, str) or normalize_exact_message_id(tid) == 0:
        raise ValueError("runtime TID must be a nonzero full TID")


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
class AdmissionRecord:
    """Immutable Manager-authored PostgreSQL launch obligation [MF-5]."""

    connection_budget: int
    connection_owner: str
    submitter_tid: str | None = None

    def __post_init__(self) -> None:
        if type(self.connection_budget) is not int or self.connection_budget <= 0:
            raise ValueError("connection_budget must be a positive integer")
        if (
            not isinstance(self.connection_owner, str)
            or re.fullmatch(r"[!-~]{1,54}", self.connection_owner) is None
        ):
            raise ValueError("connection_owner must be 1-54 nonspace ASCII characters")
        if self.submitter_tid is not None:
            _validate_runtime_tid(self.submitter_tid)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> AdmissionRecord:
        """Validate system launch evidence at its transport boundary [MF-5]."""
        if set(payload) != {"connection_budget", "connection_owner", "submitter_tid"}:
            raise ValueError("admission record has unsupported or missing fields")
        return cls(
            connection_budget=payload["connection_budget"],
            connection_owner=payload["connection_owner"],
            submitter_tid=payload["submitter_tid"],
        )

    def to_dict(self) -> dict[str, Any]:
        """Return detached transport/snapshot data [MF-5]."""
        return {
            "connection_budget": self.connection_budget,
            "connection_owner": self.connection_owner,
            "submitter_tid": self.submitter_tid,
        }


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Identity installed once in a dedicated wrapper or host worker [MF-1]."""

    tid: str
    context: str
    backend_name: str
    admission: AdmissionRecord | None = None
    broker_target: BrokerTarget | str | None = None
    context_root: str | None = None
    config: Config | None = None
    submitter_tid: str | None = None

    def __post_init__(self) -> None:
        _validate_runtime_tid(self.tid)
        object.__setattr__(
            self, "broker_target", _runtime_target_snapshot(self.broker_target)
        )
        if not self.context or not self.backend_name:
            raise ValueError("runtime context and backend must be nonempty")
        if not self.context.startswith(f"{self.backend_name}:"):
            raise ValueError("runtime context does not match its backend")
        if (
            self.broker_target is not None
            and canonical_runtime_context(self.broker_target) != self.context
        ):
            raise ValueError("runtime broker target does not match its context")
        if self.submitter_tid is not None:
            _validate_runtime_tid(self.submitter_tid)
        if self.admission is not None:
            if self.backend_name != "postgres":
                raise ValueError("connection admission evidence requires PostgreSQL")
            if self.admission.connection_owner != connection_owner_label(
                self.tid, self.context
            ):
                raise ValueError("admission owner does not match runtime identity")
            if self.admission.submitter_tid != self.submitter_tid:
                raise ValueError("admission submitter does not match runtime identity")

    def to_dict(self) -> dict[str, Any]:
        """Carry data, never a live broker resource, across spawn [SB-0.4]."""
        target = self.broker_target
        assert not isinstance(target, str)
        return {
            "tid": self.tid,
            "context": self.context,
            "backend_name": self.backend_name,
            "admission": self.admission.to_dict() if self.admission else None,
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
            "admission",
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
        raw_admission = payload.get("admission")
        if raw_admission is not None and not isinstance(raw_admission, Mapping):
            raise ValueError("runtime admission must be a record")
        raw_config = payload.get("config")
        return cls(
            tid=payload["tid"],
            context=payload["context"],
            backend_name=payload["backend_name"],
            admission=AdmissionRecord.from_mapping(raw_admission)
            if raw_admission is not None
            else None,
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

        owner = (
            identity.admission.connection_owner
            if identity.admission is not None
            else connection_owner_label(identity.tid, identity.context)
        )
        bind_connection_owner(owner)
    _runtime_identity = identity
