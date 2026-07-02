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

## Security model

This plugin's preflight validation only checks that
`spec.runner.options.profile` exists on disk (not that it is a regular,
non-empty file) and that the configured `sandbox_binary` (default
`sandbox-exec`) is on `PATH`; at construction time the option only needs to
be a non-empty string. **It does not parse or validate the profile's
contents.** Isolation is exactly what the supplied Seatbelt profile grants —
nothing more, nothing less. A profile that reads `(allow default)` grants no
isolation at all; the plugin runs it without complaint. Writing a correctly
restrictive profile is the caller's responsibility.

`sandbox-exec` is an Apple-deprecated, undocumented mechanism: Apple has
marked the command deprecated for years and publishes no supported API or
stability guarantee for the Seatbelt profile language it consumes. Treat
this runner as a best-effort, host-specific containment tool, not a
supported or guaranteed sandbox boundary — consistent with Weft's broader
trust model (`docs/specifications/00-Overview_and_Architecture.md`,
"Observability and Security"): user-level trust, not hostile
multi-tenancy, with the OS/filesystem as the actual security boundary.

Release tag:

- `weft_macos_sandbox/vX.Y.Z`
