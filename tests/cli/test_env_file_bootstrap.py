"""CLI bootstrap env-file tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from weft.bootstrap import EnvFileError, parse_env_file

pytestmark = [pytest.mark.sqlite_only]


def _clean_subprocess_env(env_file: Path | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("WEFT_") and not key.startswith("BROKER_")
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_file is not None:
        env["WEFT_ENV_FILE"] = str(env_file)
    return env


def _run_module(
    module: str,
    *args: str,
    cwd: Path,
    env_file: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_clean_subprocess_env(env_file) if env is None else env,
        timeout=30,
    )


def _probe_default_export_path(
    module: str,
    *,
    cwd: Path,
    env_file: Path,
) -> subprocess.CompletedProcess[str]:
    script = "\n".join(
        [
            "from __future__ import annotations",
            "import importlib",
            "import runpy",
            "import sys",
            f"sys.argv = [{module!r}, '--version']",
            "try:",
            f"    runpy.run_module({module!r}, run_name='__main__')",
            "except SystemExit:",
            "    pass",
            "cli_app = importlib.import_module('weft.cli.app')",
            "print(cli_app.default_export_path_help)",
        ]
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_clean_subprocess_env(env_file),
        timeout=30,
    )


def test_parse_env_file_accepts_supported_lines(tmp_path: Path) -> None:
    path = tmp_path / "weft.env"
    parsed = parse_env_file(
        """
        # comment
        export WEFT_ALPHA=one
        WEFT_BETA='two words'
        WEFT_GAMMA="three words"
        EMPTY=
        PATH=/usr/bin:/bin
        """,
        path=path,
    )

    assert parsed == {
        "WEFT_ALPHA": "one",
        "WEFT_BETA": "two words",
        "WEFT_GAMMA": "three words",
        "EMPTY": "",
        "PATH": "/usr/bin:/bin",
    }


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("1BAD=value", "invalid environment variable name"),
        ("WEFT_BAD", "expected KEY=VALUE assignment"),
        ("export", "expected KEY=VALUE assignment"),
        ("WEFT_BAD='unterminated", "quoted value must end"),
    ],
)
def test_parse_env_file_rejects_malformed_lines(
    tmp_path: Path,
    line: str,
    message: str,
) -> None:
    path = tmp_path / "weft.env"

    with pytest.raises(EnvFileError, match=message):
        parse_env_file(line, path=path)


def test_python_m_weft_loads_env_file_before_cli_import(tmp_path: Path) -> None:
    env_file = tmp_path / "weft.env"
    env_file.write_text("WEFT_DIRECTORY_NAME=.envfile-weft\n", encoding="utf-8")

    result = _run_module("weft", "system", "dump", cwd=tmp_path, env_file=env_file)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".envfile-weft" / "weft_export.jsonl").exists()


def test_python_m_weft_cli_loads_env_file_before_cli_import(tmp_path: Path) -> None:
    env_file = tmp_path / "weft.env"
    env_file.write_text("WEFT_DIRECTORY_NAME=.envfile-cli\n", encoding="utf-8")

    result = _run_module(
        "weft.cli",
        "system",
        "dump",
        cwd=tmp_path,
        env_file=env_file,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".envfile-cli" / "weft_export.jsonl").exists()


@pytest.mark.parametrize("module", ["weft", "weft.cli"])
def test_env_file_applies_before_cli_app_import(
    tmp_path: Path,
    module: str,
) -> None:
    env_file = tmp_path / "weft.env"
    env_file.write_text("WEFT_DIRECTORY_NAME=.import-order\n", encoding="utf-8")

    result = _probe_default_export_path(module, cwd=tmp_path, env_file=env_file)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == ".import-order/weft_export.jsonl"


def test_env_file_values_participate_in_context_resolution(tmp_path: Path) -> None:
    broker_dir = tmp_path / "broker-location"
    env_file = tmp_path / "weft.env"
    env_file.write_text(
        "\n".join(
            [
                f"WEFT_DEFAULT_DB_LOCATION={broker_dir}",
                "WEFT_DEFAULT_DB_NAME=envfile.db",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_module(
        "weft.cli",
        "queue",
        "write",
        "envfile.queue",
        "ok",
        cwd=tmp_path,
        env_file=env_file,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "envfile.db").exists()
    assert not (tmp_path / ".weft" / "broker.db").exists()


def test_process_env_wins_over_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "weft.env"
    env_file.write_text("WEFT_DIRECTORY_NAME=.from-file\n", encoding="utf-8")
    env = _clean_subprocess_env(env_file)
    env["WEFT_DIRECTORY_NAME"] = ".from-process"

    result = _run_module("weft.cli", "system", "dump", cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".from-process" / "weft_export.jsonl").exists()
    assert not (tmp_path / ".from-file" / "weft_export.jsonl").exists()


def test_missing_env_file_fails_before_cli_import(tmp_path: Path) -> None:
    missing = tmp_path / "missing.env"

    result = _run_module("weft.cli", "--version", cwd=tmp_path, env_file=missing)

    assert result.returncode == 2
    assert str(missing) in result.stderr
    assert "file does not exist" in result.stderr
    assert result.stdout == ""
    assert "Weft: the durable task substrate for agent systems" not in result.stderr


def test_malformed_env_file_does_not_echo_secret_value(tmp_path: Path) -> None:
    env_file = tmp_path / "weft.env"
    env_file.write_text("WEFT_SECRET='super-secret\n", encoding="utf-8")

    result = _run_module("weft.cli", "--version", cwd=tmp_path, env_file=env_file)

    assert result.returncode == 2
    assert f"{env_file}:1" in result.stderr
    assert "quoted value must end" in result.stderr
    assert "super-secret" not in result.stderr


def test_env_file_does_not_recurse(tmp_path: Path) -> None:
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text(
        f"WEFT_ENV_FILE={second}\nWEFT_DIRECTORY_NAME=.first\n",
        encoding="utf-8",
    )
    second.write_text("WEFT_DIRECTORY_NAME=.second\n", encoding="utf-8")

    result = _run_module(
        "weft.cli",
        "system",
        "dump",
        cwd=tmp_path,
        env_file=first,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".first" / "weft_export.jsonl").exists()
    assert not (tmp_path / ".second" / "weft_export.jsonl").exists()
