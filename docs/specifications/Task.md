# Tasks

**The Task Process (Self-terminating QueueWatcher):**
1. Double-forks/spawns to become completely independent from the calling process
2. Creates a ResourceMonitor instance based on the `monitor_class` value in the spec
3. Uses the ResourceMonitor to spawn and monitor the target (callable/command) in a subprocess
4. Watches any input queues (inbox, ctrl_in, others provided in taskspec) and handles incoming messages
4. Receives measurements from the ResourceMonitor at each `polling_interval`
5. Enforces resource limits, killing the target subprocess if limits are exceeded
6. Updates TaskSpec state values and reports to `weft.tasks.log` per `reporting_interval`
7. Streams or accumulates output from the target subprocess to the outbox queue
8. Performs cleanup and final reporting when the target completes

**The ResourceMonitor (Utility class within Task process):**
1. Spawns the target in a subprocess using multiprocessing (for functions) or subprocess (for commands)
2. Monitors the target subprocess using psutil
3. Provides a clean API for the Task to:
   - Get periodic measurements (memory, CPU, fds, connections)
   - Terminate the target cleanly

### 1.1 State Machine
A Task progresses through a well-defined, forward-only state machine. The Task reports on its state by updating the "state" variables in the taskspec and sending the updated taskspec to the `weft.tasks.log` queue on the schedule defined by the `reporting_interval` in the taskspec. 

-   **Legal States:** `created`, `spawning`, `running`, `completed`, `failed`, `timeout`, `cancelled`, `killed`
-   **Transitions:**
    ```
    created → spawning → running → completed 
                       ↘ failed
                       ↘ timeout
                       ↘ cancelled
                       ↘ killed
    ```

A task has no inherent maximum lifetime. In general, the lifetime of a Task is tied to the lifetime of the callable it is asked to execute. The task moves to a terminal state (`completed`, `failed`, `timeout`, `cancelled`, `killed`) either due to the subprocess completing or the Task terminating it for some reason.

Note: Some ephemeral tasks may start and exit so quickly that recording every transition is not going to occur. Therefore, tasks may advance in the state machine *implicitly* by sending a later state. For example, imagine something simple like "ls" as a task. The only recorded transitions may be `created` (by the TaskRunner), `spawning` (by the Task upon spawning the task), and `completed`, with `running` never recorded. These statesFurther, because the task's various usage variables are only measured at each `polling_interval` (set to 1 second by default), a Task will also attempt to measure its values immediately upon creating the subprocess, to try to get some measurement from fast-running, ephemeral tasks.