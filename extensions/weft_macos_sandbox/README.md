# weft-macos-sandbox

macOS sandbox runner plugin for Weft.

This extension adds the `macos-sandbox` runner via the `weft.runners`
entry-point group. It currently supports one-shot `command` TaskSpecs only and
uses `sandbox-exec` with a caller-provided profile.
