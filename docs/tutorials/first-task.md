# Your First Weft Task

This tutorial walks through creating and running a task from scratch.
By the end you'll understand: project init, inline commands, task specs,
resource limits, and result collection.

## Prerequisites

```bash
uv add weft
```

## 1. Initialize a project

```bash
mkdir my-project && cd my-project
weft init
```

This creates a `.weft/` directory for task specs, pipelines, and project config.

## 2. Run an inline command

```bash
$ weft run echo "hello from weft"
hello from weft
```

Under the hood: weft started a manager (if one wasn't running), submitted a
spawn request, the manager created a Consumer process, the consumer ran
`echo`, wrote the output to an outbox queue, and weft printed it.

## 3. Run with resource limits

```bash
$ weft run --timeout 10 --memory 256 python -c "print('constrained')"
constrained
```

If the command exceeds 256 MB memory or 10 seconds, weft terminates it and
reports a `timeout` or `killed` status.

## 4. Fire and forget

```bash
$ weft run --no-wait sleep 5
1837025672140161024

$ weft status
System: OK
Tasks: 1 running

$ weft result 1837025672140161024
# (empty output -- sleep produces nothing)
```

`--no-wait` returns the task ID (TID) immediately. Use `weft result <TID>`
to collect output later.

## 5. Run a Python function

```bash
# Create a simple module
cat > processor.py << 'PYEOF'
def greet(name: str) -> str:
    return f"Hello, {name}!"
PYEOF

$ weft run --function processor:greet --arg "world"
Hello, world!
```

## 6. Create a reusable task spec

```bash
$ weft spec create my-greeter \
    --type function \
    --target processor:greet

$ weft run --spec my-greeter --arg "weft"
Hello, weft!

$ weft spec show my-greeter
# Shows the full TaskSpec JSON
```

Task specs are stored in `.weft/tasks/` and can be version-controlled.

## 7. Check task history

```bash
$ weft task list
TID                  NAME        STATUS     TIME
1837025672140161024  sleep       completed  5.0s
1837025672140161025  greet       completed  0.1s

$ weft task status 1837025672140161025
# Shows full task details including resource usage
```

## Next steps

- [TaskSpec schema reference](../specifications/02-TaskSpec.md) -- all fields and options
- [Agent tasks](../specifications/13-Agent_Runtime.md) -- LLM-powered tasks
- [Pipeline composition](../specifications/12-Pipeline_Composition_and_UX.md) -- chaining tasks
- [CLI reference](../../README.md) -- all commands
