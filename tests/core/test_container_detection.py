"""Tests for standalone container runtime detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft.helpers.container_detection import detect_container_runtime

pytestmark = [pytest.mark.shared]


def test_detect_container_runtime_prefers_dockerenv(tmp_path: Path) -> None:
    (tmp_path / ".dockerenv").write_text("", encoding="utf-8")
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "hostname").write_text("abc123\n", encoding="utf-8")

    detection = detect_container_runtime(
        environ={},
        root=tmp_path,
        proc_root=tmp_path / "proc",
        use_systemd_detect_virt=False,
    )

    assert detection is not None
    assert detection.runtime == "docker"
    assert detection.identifier == "abc123"
    assert detection.markers == ("dockerenv",)


def test_detect_container_runtime_reads_podman_containerenv(tmp_path: Path) -> None:
    marker = tmp_path / "run" / ".containerenv"
    marker.parent.mkdir()
    marker.write_text('engine="podman-5.0.0"\n', encoding="utf-8")

    detection = detect_container_runtime(
        environ={"HOSTNAME": "podman-container"},
        root=tmp_path,
        proc_root=tmp_path / "proc",
        use_systemd_detect_virt=False,
    )

    assert detection is not None
    assert detection.runtime == "podman"
    assert detection.identifier == "podman-container"
    assert detection.markers == ("containerenv",)


def test_detect_container_runtime_uses_kubernetes_env(tmp_path: Path) -> None:
    detection = detect_container_runtime(
        environ={"KUBERNETES_SERVICE_HOST": "10.0.0.1"},
        root=tmp_path,
        proc_root=tmp_path / "proc",
        use_systemd_detect_virt=False,
    )

    assert detection is not None
    assert detection.runtime == "kubernetes"
    assert detection.markers == ("KUBERNETES_SERVICE_HOST",)


def test_detect_container_runtime_uses_cgroup_hints(tmp_path: Path) -> None:
    proc = tmp_path / "proc" / "1"
    proc.mkdir(parents=True)
    (proc / "cgroup").write_text(
        "0::/kubepods.slice/pod123/cri-containerd.scope\n",
        encoding="utf-8",
    )

    detection = detect_container_runtime(
        environ={},
        root=tmp_path,
        proc_root=tmp_path / "proc",
        use_systemd_detect_virt=False,
    )

    assert detection is not None
    assert detection.runtime == "kubernetes"
    assert detection.markers == ("proc-1/cgroup",)


@pytest.mark.parametrize(
    "cgroup_path",
    [
        "/system.slice/docker.service",
        "/system.slice/containerd.service",
        "/docker.slice/monitoring.scope",
    ],
)
def test_detect_container_runtime_ignores_host_systemd_cgroups(
    tmp_path: Path,
    cgroup_path: str,
) -> None:
    proc = tmp_path / "proc" / "1"
    proc.mkdir(parents=True)
    (proc / "cgroup").write_text(f"0::{cgroup_path}\n", encoding="utf-8")

    detection = detect_container_runtime(
        environ={},
        root=tmp_path,
        proc_root=tmp_path / "proc",
        use_systemd_detect_virt=False,
    )

    assert detection is None


def test_detect_container_runtime_uses_docker_systemd_scope(
    tmp_path: Path,
) -> None:
    proc = tmp_path / "proc" / "1"
    proc.mkdir(parents=True)
    (proc / "cgroup").write_text(
        "0::/system.slice/docker-0123456789abcdef.scope\n",
        encoding="utf-8",
    )

    detection = detect_container_runtime(
        environ={},
        root=tmp_path,
        proc_root=tmp_path / "proc",
        use_systemd_detect_virt=False,
    )

    assert detection is not None
    assert detection.runtime == "docker"
    assert detection.markers == ("proc-1/cgroup",)


def test_detect_container_runtime_returns_none_without_signals(tmp_path: Path) -> None:
    detection = detect_container_runtime(
        environ={},
        root=tmp_path,
        proc_root=tmp_path / "proc",
        use_systemd_detect_virt=False,
    )

    assert detection is None
