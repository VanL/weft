# weft-docker

Docker runner plugin for Weft.

This extension adds the `docker` runner via the `weft.runners` entry-point
group. It supports one-shot `command` TaskSpecs and the Docker-backed
one-shot provider CLI agent lane documented in Weft's agent runtime spec.

**Security note.** Weft assumes user-level trust (see
`docs/specifications/00-Overview_and_Architecture.md`).
`spec.runner.options.docker_args` is a passthrough to `docker run`: managed flags
(`--memory`, `--network`, `--volume`, …) are validated against collisions, but
escape-hatch flags such as `--privileged`, `--cap-add`, `--security-opt`,
`--pid=host`, and `-v /:/host` are not blocked. A task author who can set
`docker_args` can therefore weaken or defeat the container isolation; treat
`docker_args` as equivalent to local `docker run` access, not as a sandbox
boundary.

Current host support:

- Linux: supported
- macOS: supported
- Windows: not currently supported

## Container Profiles

Command tasks can select a project-local Docker container profile through
`spec.runner.options.container_profile`. Profiles are useful when the Weft
manager runs on the host but the task must run inside a Docker network where
service names such as `db`, `redis`, or `internal-api` resolve.

TaskSpec shape:

```json
{
  "spec": {
    "type": "command",
    "process_target": "python3",
    "args": ["-m", "my_project.probe"],
    "runner": {
      "name": "docker",
      "options": {
        "container_profile": "ops"
      }
    }
  }
}
```

By default, profiles are loaded from `.weft/docker-profiles.toml`. A task can
override that with `spec.runner.options.container_profile_file`.

```toml
version = 1

# Optional. Relative values are resolved from this file's directory.
root = ".."

[profiles.ops]
image = "ghcr.io/example/project:latest"
network = "project_ops"
mount_workdir = false
container_workdir = "/app/project"
env_from_host = ["OPTIONAL_TOKEN"]
required_env_from_host = ["REQUIRED_TOKEN"]

[profiles.ops.env]
SERVICE_URL = "https://internal-api:8443"

[[profiles.ops.mounts]]
source = "config"
target = "/app/project/config"
read_only = true
```

Profile values are defaults. Explicit TaskSpec `spec.runner.options` and
`spec.env` win over profile values. `env_from_host` forwards named host
environment variables when present. `required_env_from_host` requires the
merged task environment to contain the named value, either from host env,
profile env, or explicit TaskSpec env.

Relative profile mount and build paths resolve against
`container_profile_root`, then top-level TOML `root`, then the profile file's
directory. Runtime checks for named Docker networks and profile-sourced mount
or build paths run only during explicit preflight validation.

Container profiles currently apply to command tasks only. Docker-backed agent
tasks continue to use provider-specific image recipes, container runtime
descriptors, and `work_item_mounts`.

Release tag:

- `weft_docker/vX.Y.Z`
