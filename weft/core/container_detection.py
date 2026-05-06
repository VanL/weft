"""Container runtime detection helpers.

These helpers identify whether the current process appears to be running inside
a Linux container or FreeBSD jail. Detection is intentionally best-effort: the
result is suitable for choosing conservative runtime-handle authority, not for
security boundaries.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-7]
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_CGROUP_RUNTIME_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("podman", ("libpod", "podman")),
    ("docker", ("docker",)),
    ("kubernetes", ("kubepods",)),
    ("containerd", ("containerd", "cri-containerd", "cri-")),
)


@dataclass(frozen=True, slots=True)
class ContainerRuntimeDetection:
    """Best-effort container or jail runtime detection result."""

    runtime: str
    markers: tuple[str, ...]
    identifier: str | None = None

    def observations(self, *, container_pid: int | None = None) -> dict[str, object]:
        """Return JSON-friendly runtime observations."""

        payload: dict[str, object] = {
            "container_runtime": self.runtime,
            "container_markers": list(self.markers),
        }
        if self.identifier:
            payload["container_id"] = self.identifier
        if container_pid is not None:
            payload["container_pid"] = container_pid
        return payload


def detect_container_runtime(
    *,
    environ: Mapping[str, str] | None = None,
    root: Path | str = Path("/"),
    proc_root: Path | str = Path("/proc"),
    use_systemd_detect_virt: bool = True,
) -> ContainerRuntimeDetection | None:
    """Return container/jail runtime evidence for the current process, if any."""

    env = os.environ if environ is None else environ
    root_path = Path(root)
    proc_path = Path(proc_root)

    jail = _detect_freebsd_jail()
    if jail is not None:
        return jail

    hostname = _container_identifier(env=env, root=root_path)
    docker_marker = root_path / ".dockerenv"
    if docker_marker.is_file():
        return ContainerRuntimeDetection(
            runtime="docker",
            markers=("dockerenv",),
            identifier=hostname,
        )

    containerenv = root_path / "run" / ".containerenv"
    if containerenv.is_file():
        runtime = _runtime_from_containerenv(containerenv) or "container"
        return ContainerRuntimeDetection(
            runtime=runtime,
            markers=("containerenv",),
            identifier=hostname,
        )

    kube_host = env.get("KUBERNETES_SERVICE_HOST")
    if isinstance(kube_host, str) and kube_host.strip():
        return ContainerRuntimeDetection(
            runtime="kubernetes",
            markers=("KUBERNETES_SERVICE_HOST",),
            identifier=hostname,
        )

    container_env = env.get("container")
    if isinstance(container_env, str) and container_env.strip():
        return ContainerRuntimeDetection(
            runtime=container_env.strip(),
            markers=("container-env",),
            identifier=hostname,
        )

    cgroup_detection = _detect_from_cgroups(proc_path, identifier=hostname)
    if cgroup_detection is not None:
        return cgroup_detection

    if use_systemd_detect_virt:
        return _detect_with_systemd(identifier=hostname)
    return None


def _detect_freebsd_jail() -> ContainerRuntimeDetection | None:
    if platform.system() != "FreeBSD":
        return None
    try:
        result = subprocess.run(
            ["sysctl", "-n", "security.jail.jailed"],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.stdout.strip() == "1":
        return ContainerRuntimeDetection(runtime="jail", markers=("freebsd-jail",))
    return None


def _container_identifier(*, env: Mapping[str, str], root: Path) -> str | None:
    hostname = env.get("HOSTNAME")
    if isinstance(hostname, str) and hostname.strip():
        return hostname.strip()
    try:
        value = (root / "etc" / "hostname").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _runtime_from_containerenv(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'^engine=["\']?([^"\'\n]+)', text, flags=re.MULTILINE)
    if match is None:
        return None
    engine = match.group(1).strip().lower()
    if "podman" in engine:
        return "podman"
    if "docker" in engine:
        return "docker"
    return "container"


def _detect_from_cgroups(
    proc_root: Path,
    *,
    identifier: str | None,
) -> ContainerRuntimeDetection | None:
    for relative_path in ("1/cgroup", "self/cgroup"):
        path = proc_root / relative_path
        try:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        for runtime, patterns in _CGROUP_RUNTIME_PATTERNS:
            if any(pattern in text for pattern in patterns):
                return ContainerRuntimeDetection(
                    runtime=runtime,
                    markers=(f"proc-{relative_path}",),
                    identifier=identifier,
                )
    return None


def _detect_with_systemd(
    *,
    identifier: str | None,
) -> ContainerRuntimeDetection | None:
    executable = shutil.which("systemd-detect-virt")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "--container"],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    runtime = result.stdout.strip()
    if result.returncode == 0 and runtime and runtime != "none":
        return ContainerRuntimeDetection(
            runtime=runtime,
            markers=("systemd-detect-virt",),
            identifier=identifier,
        )
    return None
