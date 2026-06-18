# weft-microsandbox

Microsandbox runner plugin for Weft.

This extension runs disposable command and one-shot `provider_cli` agent tasks
inside Microsandbox microVM sandboxes. It is intended for probably-hostile tools,
MCP servers, and skills that need a harder process and filesystem boundary than
the host runner.

The runner name is `microsandbox`.

Install with:

```bash
uv add 'weft[microsandbox]'
```

The current Microsandbox SDK supports Linux x86_64/aarch64 and macOS Apple
Silicon. The SDK is Apache-2.0 licensed. A working local Microsandbox runtime
is required for real execution; `weft spec validate --preflight --load-runner`
checks the local runtime gate explicitly.

## Tool Mode

```json
{
  "spec": {
    "type": "command",
    "process_target": "python",
    "args": ["-c", "print('hello')"],
    "runner": {
      "name": "microsandbox",
      "options": {
        "image": "python:3.12-alpine",
        "mode": "tool",
        "network": "none",
        "workspace_mode": "none"
      }
    }
  }
}
```

## Agent Mode

```json
{
  "spec": {
    "type": "agent",
    "persistent": false,
    "agent": {
      "runtime": "provider_cli",
      "conversation_scope": "per_message",
      "runtime_config": {
        "provider": "codex"
      }
    },
    "runner": {
      "name": "microsandbox",
      "options": {
        "image": "ghcr.io/acme/codex-provider:latest",
        "mode": "agent",
        "executable": "codex",
        "network": "allow",
        "workspace_mode": "none"
      }
    }
  }
}
```

`runner.options.executable` is the executable inside the guest image. It is not
resolved on the host.

## Runner Options

- `image`: required OCI image.
- `mode`: optional; derived from `spec.type` as `tool` for command tasks and
  `agent` for agent tasks.
- `executable`: required for agent mode; guest-local provider CLI command.
- `network`: `none` by default, or `allow`.
- `workspace_mode`: `none` by default, or `copy`, `mount-read-only`, or
  `mount-read-write`.
- `mounts`: explicit host mounts. Entries default to read-only.
- `cwd`: guest working directory. Required for workspace modes.
- `sandbox_name_prefix`: optional runtime name prefix.

## Security Defaults

- `network` defaults to `none`.
- `workspace_mode` defaults to `none`.
- host environment variables are not forwarded unless they are explicit in
  `spec.env`.
- persistent and interactive tasks are rejected.

## Current Limitations

Memory, CPU, and file-descriptor limits are passed to the Microsandbox SDK, but
the runner does not yet map SDK OOM or metrics evidence to
`RunnerOutcome(status="limit")`. A guest killed by the runtime for memory
pressure may currently surface as a generic execution error.

This runner is a process, VM, and filesystem boundary. It is not a semantic
defense against prompt injection or malicious output.
