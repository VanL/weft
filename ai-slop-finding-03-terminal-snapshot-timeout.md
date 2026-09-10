# Finding 03: A duplicated observer loop bypasses its own deadline

Date: 2026-09-08. Original audit baseline: `b605582c` with then-visible correction work. Current-state check: `178e3a34`; the relevant branch remains present. This is a non-normative review record, not an implementation plan or authorization. Confidence is high in the timeout defect and its control-flow cause.

## Claim and code evidence

In [weft/commands/tasks.py](weft/commands/tasks.py), `task_terminal_snapshot` promises a bounded, non-consuming known-TID observation. It computes a deadline for positive timeouts, then repeatedly asks `task_evidence.known_tid_evidence` for an observation. Around line 387:

```python
if snapshot.status in {"running", "pending"} and deadline is not None:
    time.sleep(min(TASK_EVIDENCE_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
    continue
```

That branch never checks whether the deadline expired. After expiry the sleep becomes zero, and the loop continues acquiring broker evidence. The expiry check lower in the function is reachable only when no evidence was returned. Continuing nonterminal evidence therefore defeats the bound.

The problem is not the named polling interval. It is the duplication of wait policy across observer branches: a timeout is represented and partially enforced, but the main “task still exists” path skips the termination decision. The same codebase has other wait loops that explicitly decide expiry before continuing. A helper name and a deadline variable make this implementation look more complete than it is.

## Executed evidence and limits

The audit wrote one real `work_started` task-log row into a temporary broker, with a one-shot function TaskSpec in `running` state and no terminal output or runtime mapping. Through the public client, the immediate observation returned `pending`. A child process then called the same API with a positive timeout:

```text
timeout=0 snapshot: pending
starting timeout=.05 snapshot

subprocess still running at 2 seconds; supervisor killed it
```

The supervisor used `subprocess.run(..., timeout=2)` and a temporary directory. No manager was launched. The output proves that a 0.05-second budget failed to bound an ordinary continuing observation; it does not claim every backend call is itself interruptible. The final bounded probe used a separate process because raising an ordinary exception from a signal handler can be caught by defensive broker reads.

The probe ran during the original audit. Current code was reread at `178e3a34`, not reprobed for this document. Tests in [test_task_commands.py](tests/commands/test_task_commands.py) exercise immediate terminal snapshots, acknowledgment, and several deadline helpers, but the audited coverage lacked this positive-timeout/nonterminal path.

## History and governing behavior

Commit `4167f1934` (2026-05-06, “Add shared task evidence reconciliation”) introduced the current evidence branch. Commit `620cceb43` (2026-07-03) clamped the sleep to the remaining deadline without adding an expiry exit to that branch.

[Message Flow and State, MF-5](docs/specifications/05-Message_Flow_and_State.md) governs evidence classification. The current [Public Python Client Surface, IP-1.1](docs/specifications/09-Implementation_Plan.md), including its “Task terminal observation” row, requires non-consuming terminal snapshots and explicit acknowledgment. The [integration README, Submission Handle](integrations/weft_django/README.md) documents the shipped Django wrapper's read-only use of that path. A local wait deadline is not authority to publish a task timeout or consume a result.

[Django design, DJ-8.3 and DJ-10.1](docs/specifications/13C-Using_Weft_With_Django.md) is supporting design/history evidence only. The [specification index](docs/specifications/README.md) classifies 13C as proposed and exploratory, not current core behavior; it is not the governing contract for this finding.

## Preferable design and verification

Make each iteration acquire an observation, decide whether it is terminal, decide whether the caller's budget is exhausted, and only then wait again. Preserve the latest honest nonterminal snapshot on expiry if that is the selected API contract. If the API instead needs a timeout exception, specify that explicitly; do not change task lifecycle state.

The strongest counterargument is that runtime and broker probes can have their own timing constraints. This fix alone cannot establish a hard wall-clock bound over blocking backend operations. It can, however, remove the demonstrable unbounded repetition after completed probes.

Proposed verification: deterministic running and pending evidence across expiry, zero-time observation, terminal evidence before expiry, no-evidence fallback, and a bounded real-broker public-client test. Confirm non-consumption and no task-state writes. Prefer one clear wait decision over a new general-purpose scheduler or state machine.
