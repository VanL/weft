# weft-macos-sandbox

macOS sandbox runner plugin for Weft.

This extension adds the `macos-sandbox` runner via the `weft.runners`
entry-point group. It currently supports one-shot `command` TaskSpecs only and
uses `sandbox-exec` with a caller-provided profile.

## Environment variables

The sandboxed child process does **not** inherit the full host environment.
By default only a fixed baseline of session-plumbing variables is forwarded:

- `HOME`
- `LANG`
- `LC_ALL`
- `LC_CTYPE`
- `LOGNAME`
- `PATH`
- `SHELL`
- `TERM`
- `TMPDIR`
- `USER`

To forward additional host-derived values, opt in explicitly via
`spec.runner.options.env_passthrough`:

```json
{
  "runner": {
    "name": "macos-sandbox",
    "options": {
      "profile": "/path/to/sandbox.sb",
      "env_passthrough": ["MY_HOST_TOKEN"]
    }
  }
}
```

For fixed values (not derived from the host), set `spec.env` instead.
`spec.env` always wins over both the baseline and `env_passthrough`, so
TaskSpec-declared values cannot be shadowed by host state.

**Migration note:** weft-macos-sandbox releases before 0.6.0 inherited the
full host environment; set `env_passthrough` explicitly if you depended on
that.

Release tag:

- `weft_macos_sandbox/vX.Y.Z`
