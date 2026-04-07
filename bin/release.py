#!/usr/bin/env python3
"""Repo-local release helper for Weft maintainers."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
PYPROJECT_PATH: Final[Path] = PROJECT_ROOT / "pyproject.toml"
CONSTANTS_PATH: Final[Path] = PROJECT_ROOT / "weft" / "_constants.py"
UV_LOCK_PATH: Final[Path] = PROJECT_ROOT / "uv.lock"
RELEASE_GATE_WORKFLOW: Final[str] = ".github/workflows/release-gate.yml"
RELEASE_PROJECT_NAME: Final[str] = "weft"
GITHUB_API_BASE: Final[str] = "https://api.github.com"
PYPI_API_BASE: Final[str] = "https://pypi.org/pypi"
HTTP_TIMEOUT_SECONDS: Final[float] = 10.0
VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d+\.\d+\.\d+$")
PYPROJECT_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'(?m)^version = "([^"]+)"$'
)
CONSTANTS_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'(?m)^__version__:\s*Final\[str\]\s*=\s*"([^"]+)"$'
)

PRECHECK_COMMANDS: Final[tuple[tuple[str, ...], ...]] = (
    (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "pytest",
        "-v",
        "--tb=short",
        "-m",
        "",
        "--override-ini=addopts=-ra -q --strict-markers -n auto --dist loadgroup",
    ),
    (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "bin/pytest-pg",
        "--all",
    ),
    (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "ruff",
        "check",
        "weft",
    ),
    (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "ruff",
        "format",
        "--check",
        "weft",
    ),
    (
        "uv",
        "run",
        "--extra",
        "dev",
        "--extra",
        "docker",
        "--extra",
        "macos-sandbox",
        "mypy",
        "weft",
        "--config-file",
        "pyproject.toml",
    ),
)
POSTUPDATE_COMMANDS: Final[tuple[tuple[str, ...], ...]] = (
    ("uv", "run", "pytest", "tests/system/test_constants.py", "-q"),
    ("uv", "build"),
)
TagAction = Literal[
    "create",
    "push_local",
    "replace_local",
    "replace_remote",
    "reuse_remote",
]


@dataclass(frozen=True, slots=True)
class ReleaseState:
    """Observed publication and tag state for a release version."""

    version: str
    tag_name: str
    github_release_exists: bool
    pypi_release_exists: bool
    local_tag_commit: str | None
    remote_tag_commit: str | None

    @property
    def published(self) -> bool:
        """Whether the version was externally published."""

        return self.github_release_exists or self.pypi_release_exists


def validate_version(version: str) -> str:
    """Validate the explicit release version."""
    normalized = version.strip()
    if not VERSION_PATTERN.fullmatch(normalized):
        raise ValueError("Version must use X.Y.Z format, for example: 0.1.1")
    return normalized


def _extract_version(
    path: Path,
    pattern: re.Pattern[str],
    *,
    label: str,
) -> str:
    text = path.read_text(encoding="utf-8")
    match = pattern.search(text)
    if match is None:
        raise RuntimeError(f"Could not find version in {label}: {path}")
    return match.group(1)


def read_current_version(
    *,
    pyproject_path: Path = PYPROJECT_PATH,
    constants_path: Path = CONSTANTS_PATH,
) -> str:
    """Read and verify the current repo version."""
    pyproject_version = _extract_version(
        pyproject_path,
        PYPROJECT_VERSION_PATTERN,
        label="pyproject.toml",
    )
    constants_version = _extract_version(
        constants_path,
        CONSTANTS_VERSION_PATTERN,
        label="weft/_constants.py",
    )
    if pyproject_version != constants_version:
        raise RuntimeError(
            "Version mismatch between pyproject.toml "
            f"({pyproject_version}) and weft/_constants.py ({constants_version})"
        )
    return pyproject_version


def _replace_version(
    text: str,
    pattern: re.Pattern[str],
    version: str,
    *,
    label: str,
) -> str:
    updated_text, count = pattern.subn(
        lambda match: match.group(0).replace(match.group(1), version),
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Expected one version assignment in {label}, found {count}")
    return updated_text


def write_version_files(
    version: str,
    *,
    pyproject_path: Path = PYPROJECT_PATH,
    constants_path: Path = CONSTANTS_PATH,
) -> None:
    """Update the canonical version files together."""
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    constants_text = constants_path.read_text(encoding="utf-8")

    updated_pyproject = _replace_version(
        pyproject_text,
        PYPROJECT_VERSION_PATTERN,
        version,
        label="pyproject.toml",
    )
    updated_constants = _replace_version(
        constants_text,
        CONSTANTS_VERSION_PATTERN,
        version,
        label="weft/_constants.py",
    )

    pyproject_path.write_text(updated_pyproject, encoding="utf-8")
    constants_path.write_text(updated_constants, encoding="utf-8")


def _format_command(command: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_command(command: tuple[str, ...], *, dry_run: bool = False) -> None:
    """Run a command from the repo root, printing it first."""
    print(f"$ {_format_command(command)}")
    if dry_run:
        return
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def is_dirty_worktree() -> bool:
    """Return True when git reports local modifications."""
    result = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def _require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required command not found on PATH: {name}")


def _capture_command(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    """Run a command from the repo root and capture its output."""

    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git_output(command: tuple[str, ...], *, label: str) -> str:
    """Return git stdout or raise a targeted release-helper error."""

    result = _capture_command(command)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise RuntimeError(f"Unable to determine {label}: {detail}")
    return result.stdout.strip()


def current_head_commit() -> str:
    """Return the current HEAD commit SHA."""

    return _git_output(("git", "rev-parse", "HEAD"), label="current HEAD commit")


def local_tag_commit(tag_name: str) -> str | None:
    """Return the local tag commit SHA or ``None`` if the tag is absent."""

    result = _capture_command(
        ("git", "rev-parse", "-q", "--verify", f"refs/tags/{tag_name}^{{commit}}")
    )
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def remote_tag_commit(tag_name: str) -> str | None:
    """Return the origin tag commit SHA or ``None`` if the tag is absent."""

    result = _capture_command(
        (
            "git",
            "ls-remote",
            "--tags",
            "origin",
            f"refs/tags/{tag_name}",
            f"refs/tags/{tag_name}^{{}}",
        )
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise RuntimeError(f"Unable to inspect origin tag {tag_name}: {detail}")

    direct_ref = f"refs/tags/{tag_name}"
    peeled_ref = f"{direct_ref}^{{}}"
    direct_commit: str | None = None
    peeled_commit: str | None = None
    for line in result.stdout.splitlines():
        sha, ref = line.split(maxsplit=1)
        if ref == peeled_ref:
            peeled_commit = sha
        elif ref == direct_ref:
            direct_commit = sha
    return peeled_commit or direct_commit


def origin_remote_url() -> str:
    """Return the `origin` remote URL."""

    return _git_output(
        ("git", "remote", "get-url", "origin"), label="origin remote URL"
    )


def github_repo_slug_from_remote(remote_url: str) -> str | None:
    """Extract `owner/repo` from a GitHub remote URL."""

    stripped = remote_url.strip()
    if stripped.startswith("git@github.com:"):
        path = stripped.removeprefix("git@github.com:")
    elif stripped.startswith("ssh://git@github.com/"):
        path = stripped.removeprefix("ssh://git@github.com/")
    elif stripped.startswith("https://github.com/") or stripped.startswith(
        "http://github.com/"
    ):
        path = urllib_parse.urlparse(stripped).path.lstrip("/")
    else:
        return None

    if path.endswith(".git"):
        path = path[:-4]
    if path.count("/") != 1:
        return None
    owner, repo = path.split("/", maxsplit=1)
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def _url_exists(url: str) -> bool:
    """Return whether a JSON endpoint exists, treating 404 as missing."""

    headers = {
        "Accept": "application/json",
        "User-Agent": "weft-release-helper",
    }
    if url.startswith(GITHUB_API_BASE):
        headers.update(_github_api_auth_headers())

    request = urllib_request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib_request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS):
            return True
    except urllib_error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise RuntimeError(f"Unable to query {url}: HTTP {exc.code}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Unable to query {url}: {exc.reason}") from exc


def github_release_exists(tag_name: str) -> bool:
    """Return whether GitHub already has a published release for the tag."""

    remote_url = origin_remote_url()
    repo_slug = github_repo_slug_from_remote(remote_url)
    if repo_slug is None:
        raise RuntimeError(
            f"Unable to determine GitHub repository from origin remote: {remote_url}"
        )
    encoded_tag = urllib_parse.quote(tag_name, safe="")
    return _url_exists(
        f"{GITHUB_API_BASE}/repos/{repo_slug}/releases/tags/{encoded_tag}"
    )


@lru_cache(maxsize=1)
def _github_api_token() -> str | None:
    """Return an auth token for GitHub API requests when one is available."""

    for env_var in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(env_var, "").strip()
        if token:
            return token

    if shutil.which("gh") is None:
        return None

    result = _capture_command(("gh", "auth", "token"))
    if result.returncode != 0:
        return None

    token = result.stdout.strip()
    return token or None


def _github_api_auth_headers() -> dict[str, str]:
    """Return GitHub API auth headers for authenticated release lookups."""

    token = _github_api_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def pypi_version_exists(version: str) -> bool:
    """Return whether PyPI already has the project version."""

    encoded_project = urllib_parse.quote(RELEASE_PROJECT_NAME, safe="")
    encoded_version = urllib_parse.quote(version, safe="")
    return _url_exists(f"{PYPI_API_BASE}/{encoded_project}/{encoded_version}/json")


def inspect_release_state(version: str) -> ReleaseState:
    """Collect publication and tag state for a target version."""

    tag_name = f"v{version}"
    return ReleaseState(
        version=version,
        tag_name=tag_name,
        github_release_exists=github_release_exists(tag_name),
        pypi_release_exists=pypi_version_exists(version),
        local_tag_commit=local_tag_commit(tag_name),
        remote_tag_commit=remote_tag_commit(tag_name),
    )


def published_destinations(state: ReleaseState) -> str:
    """Return a human-readable list of external publication destinations."""

    destinations: list[str] = []
    if state.github_release_exists:
        destinations.append("GitHub Release")
    if state.pypi_release_exists:
        destinations.append("PyPI publication")
    return " and ".join(destinations)


def resolve_target_version(
    requested_version: str | None,
    *,
    current_version: str,
) -> tuple[str, ReleaseState]:
    """Resolve the target version and ensure it has not been externally published."""

    target_version = (
        current_version
        if requested_version is None
        else validate_version(requested_version)
    )
    state = inspect_release_state(target_version)
    if state.published:
        if requested_version is None:
            raise RuntimeError(
                f"Current version {current_version} already has a "
                f"{published_destinations(state)}. Pass --version with a new version."
            )
        raise RuntimeError(
            f"Version {target_version} already has a {published_destinations(state)}. "
            "Choose a new version."
        )
    return target_version, state


def _short_commit(commit: str) -> str:
    return commit[:12]


def plan_tag_action(
    state: ReleaseState,
    *,
    head_commit: str,
    version_changed: bool,
    allow_retag: bool,
) -> TagAction:
    """Plan how the helper should handle the target tag safely."""

    if version_changed:
        if state.remote_tag_commit is not None:
            if allow_retag:
                return "replace_remote"
            raise RuntimeError(
                f"Tag {state.tag_name} already exists on origin at "
                f"{_short_commit(state.remote_tag_commit)}. Choose a different version "
                "or pass --retag."
            )
        if state.local_tag_commit is not None:
            return "replace_local"
        return "create"

    if state.remote_tag_commit is not None and state.remote_tag_commit != head_commit:
        if allow_retag:
            return "replace_remote"
        raise RuntimeError(
            f"Tag {state.tag_name} already exists on origin at "
            f"{_short_commit(state.remote_tag_commit)}, but HEAD is "
            f"{_short_commit(head_commit)}. Reusing this unpublished version "
            "would move the remote tag; choose a new version or pass --retag."
        )

    if state.local_tag_commit is not None and state.local_tag_commit != head_commit:
        if state.remote_tag_commit is None:
            return "replace_local"
        raise RuntimeError(
            f"Tag {state.tag_name} already exists on local repo at "
            f"{_short_commit(state.local_tag_commit)}, but origin already has "
            f"{_short_commit(state.remote_tag_commit)}. Fix the local tag or "
            "delete it manually before retrying."
        )

    if state.remote_tag_commit is not None:
        return "reuse_remote"
    if state.local_tag_commit is not None:
        return "push_local"
    return "create"


def _remote_tag_reuse_note(tag_name: str) -> str:
    return (
        f"Tag {tag_name} already exists on origin at HEAD. Pushing the same tag "
        f"again will not retrigger {RELEASE_GATE_WORKFLOW}; rerun the existing "
        "release gate manually in GitHub Actions if needed."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a local Weft release")
    parser.add_argument(
        "--version",
        help=(
            "Explicit release version in X.Y.Z format. When omitted, the helper "
            "reuses the current version if it has not been published yet."
        ),
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Deprecated compatibility flag. GitHub Releases are now created by "
            "the tag-push workflow after SQLite and Postgres tests pass."
        ),
    )
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip preflight test/lint/type-check commands",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without modifying files or running commands",
    )
    parser.add_argument(
        "--retag",
        action="store_true",
        help=(
            "Delete and recreate the remote tag when the target version is still "
            "unpublished but the existing tag points at the wrong commit."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    current_version = read_current_version()
    dirty = is_dirty_worktree()

    if dirty and not args.dry_run:
        raise RuntimeError("Working tree must be clean before release.")

    target_version, release_state = resolve_target_version(
        args.version,
        current_version=current_version,
    )
    tag_name = release_state.tag_name
    version_changed = target_version != current_version
    head_commit = current_head_commit()
    tag_action = plan_tag_action(
        release_state,
        head_commit=head_commit,
        version_changed=version_changed,
        allow_retag=args.retag,
    )

    print(f"current: {current_version}")
    print(f"target:  {target_version}")
    print("status:  unpublished on GitHub Release and PyPI")

    if args.dry_run:
        if dirty:
            print("dry-run: working tree is dirty; a real release would fail")
        if args.publish:
            print(
                "--publish is ignored: "
                f"{RELEASE_GATE_WORKFLOW} publishes the distributions and "
                "creates the GitHub Release after the pushed tag passes "
                "SQLite and Postgres tests"
            )
        if not args.skip_checks:
            for command in PRECHECK_COMMANDS:
                run_command(command, dry_run=True)
        if version_changed:
            print(
                "dry-run: would update "
                f"{PYPROJECT_PATH.relative_to(PROJECT_ROOT)} and "
                f"{CONSTANTS_PATH.relative_to(PROJECT_ROOT)}"
            )
        else:
            print(
                f"dry-run: current version {target_version} is unpublished; "
                "would reuse existing version files"
            )
        for command in POSTUPDATE_COMMANDS:
            run_command(command, dry_run=True)
        if version_changed:
            for command in (
                ("git", "add", "pyproject.toml", "weft/_constants.py", "uv.lock"),
                ("git", "commit", "-m", f"Release {target_version}"),
            ):
                run_command(command, dry_run=True)
        else:
            print(
                "dry-run: no release commit needed because version files already match"
            )
        if tag_action == "replace_local":
            run_command(("git", "tag", "-d", tag_name), dry_run=True)
        if tag_action == "replace_remote":
            if release_state.local_tag_commit is not None:
                run_command(("git", "tag", "-d", tag_name), dry_run=True)
            run_command(("git", "push", "--delete", "origin", tag_name), dry_run=True)
        if tag_action in {"create", "replace_local", "replace_remote"}:
            run_command(("git", "tag", tag_name), dry_run=True)
        run_command(("git", "push"), dry_run=True)
        if tag_action in {"create", "push_local", "replace_local", "replace_remote"}:
            run_command(("git", "push", "origin", tag_name), dry_run=True)
        else:
            print(f"dry-run: {_remote_tag_reuse_note(tag_name)}")
        print(
            "dry-run: next step is to wait for "
            f"{RELEASE_GATE_WORKFLOW} to run on {tag_name}; it will publish "
            "the distributions and create the GitHub Release after the SQLite "
            "and Postgres suites pass"
        )
        return 0

    _require_command("uv")
    if args.publish:
        print(
            "--publish is ignored: "
            f"{RELEASE_GATE_WORKFLOW} publishes the distributions and creates "
            "the GitHub Release after the pushed tag passes SQLite and "
            "Postgres tests"
        )

    if not args.skip_checks:
        for command in PRECHECK_COMMANDS:
            run_command(command)

    if version_changed:
        write_version_files(target_version)
        print(
            "Updated version files: "
            f"{PYPROJECT_PATH.relative_to(PROJECT_ROOT)}, "
            f"{CONSTANTS_PATH.relative_to(PROJECT_ROOT)}"
        )
    else:
        print(
            f"Reusing current unpublished version {target_version}; version files unchanged"
        )

    for command in POSTUPDATE_COMMANDS:
        run_command(command)

    if version_changed:
        for command in (
            ("git", "add", "pyproject.toml", "weft/_constants.py", "uv.lock"),
            ("git", "commit", "-m", f"Release {target_version}"),
        ):
            run_command(command)

    if tag_action == "replace_local":
        run_command(("git", "tag", "-d", tag_name))

    if tag_action == "replace_remote":
        if release_state.local_tag_commit is not None:
            run_command(("git", "tag", "-d", tag_name))
        run_command(("git", "push", "--delete", "origin", tag_name))

    if tag_action in {"create", "replace_local", "replace_remote"}:
        run_command(("git", "tag", tag_name))

    for command in (("git", "push"),):
        run_command(command)

    if tag_action in {"create", "push_local", "replace_local", "replace_remote"}:
        run_command(("git", "push", "origin", tag_name))
    else:
        print(_remote_tag_reuse_note(tag_name))

    print(
        "Next step: wait for "
        f"{RELEASE_GATE_WORKFLOW} to run on {tag_name}; it will publish the "
        "distributions and create the GitHub Release after the SQLite and "
        "Postgres suites pass"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
