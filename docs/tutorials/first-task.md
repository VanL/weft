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
$ weft run --no-wait sleep 1
<TID>

$ weft result <TID>
# (empty output -- sleep produces nothing)
```

`--no-wait` returns the task ID (TID) immediately. Use `weft result <TID>`
to collect output later. While the task is still running, use `weft status` or
`weft task status <TID>` to inspect progress.

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
$ weft spec generate --type task > my-greeter.json
```

Edit `my-greeter.json` so it looks like this:

```json
{
  "name": "my-greeter",
  "spec": {
    "type": "function",
    "function_target": "processor:greet",
    "args": ["weft"]
  },
  "metadata": {}
}
```

Then store and run it:

```bash
$ weft spec create my-greeter --file my-greeter.json
/path/to/project/.weft/tasks/my-greeter.json

$ weft run --spec my-greeter
Hello, weft!

$ weft spec show my-greeter
# Shows the stored TaskSpec JSON
```

Task specs are stored in `.weft/tasks/` and can be version-controlled.
This example bakes `"weft"` into `spec.args`, so the stored spec runs as-is.
If you want `weft run --spec my-greeter --name value` style inputs, declare
`spec.parameterization` or `spec.run_input` in the TaskSpec. `--arg` is only
for `weft run --function`. When declared options should become the flat JSON
work payload, use `weft.builtins.run_input:arguments_payload`; when the target
function expects keyword arguments, use
`weft.builtins.run_input:keyword_arguments_payload`.

## 7. Check task history

```bash
$ weft task list
# Shows current tasks, including their TIDs, status, runner, and target summary

$ weft task status <TID>
# Shows the current status line for that task
```

## Next steps

- [TaskSpec schema reference](../specifications/02-TaskSpec.md) -- all fields and options
- [Agent tasks](../specifications/13-Agent_Runtime.md) -- LLM-powered tasks
- [Pipeline composition](../specifications/12-Pipeline_Composition_and_UX.md) -- chaining tasks
- [CLI reference](../../README.md) -- all commands
