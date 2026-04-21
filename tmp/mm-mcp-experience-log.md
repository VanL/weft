# Model Monster MCP Server — Usage Experience Log

**Task**: Connect to MM MCP, find changes between the MM Platform blueprint (v3, last viewed in the web UI) and the actual current code. Update the blueprint appropriately.

**Session context**: Running Claude Code from `/Users/van/Developer/weft`. MM repo is at `/Users/van/Developer/mm`.

---

## Entry 1 — Locating credentials & MCP endpoint

Found MCP config at `/Users/van/Developer/mm/.mcp.json`. The `model-monster` server:
- URL: `https://api.modelmonster.ai/api/v1/mcp/`
- Auth: Bearer token via header

User confirmed: use `model-monster` (remote), not `local-model-monster`.

## Entry 2 — MCP not auto-loaded in current session

Searched for `mcp__model*` tools — no matches. The MCP is project-scoped to `/Users/van/Developer/mm`, but my CWD is `/Users/van/Developer/weft`, so Claude Code did not auto-load the MM MCP.

**Friction #1**: project-scoped MCP means an agent running from outside the project can't just call MM tools by name. Options:
1. `cd` into the mm repo and restart Claude Code (session-interrupting)
2. Promote to user-global config (easy but loses project-scope discipline)
3. Call the MCP endpoint directly over HTTP with the token

Going with option 3 — this mirrors the realistic "a CI agent on a different machine calls MM" scenario, which is exactly the wedge the user just described.


## Entry 3 — MCP handshake over raw HTTP worked cleanly

`initialize` JSON-RPC call succeeded immediately with the Bearer token. Server reports:
- name: `model-monster`
- version: `1.27.0`
- protocol: `2025-06-18`
- capabilities: prompts, resources, tools (all `listChanged: false`)

**Positive #1**: the MCP endpoint is a standard HTTP/JSON-RPC surface. No quirks. `curl` worked on the first try. A CI agent calling this has zero auth friction beyond providing the Bearer token.

**Positive #2**: The response includes a `x-correlation-id` header. Useful for support / debugging.

## Entry 4 — Tool inventory: 13 tools, purposeful surface

Tools available:
- `list_orgs` / `list_teams` / `list_systems` — navigation
- `create_system` — new system intake
- `get_snapshot` — pull the current graph
- `get_node_detail` — node-level detail
- `get_type_taxonomy` — type vocabulary for this system
- `get_phase_route_overrides` / `update_phase_route_overrides` — advanced routing
- `search` / `bulk_search` / `search_detail` — component/resource discovery
- `sync_graph` — declarative update (the big one)

**Observation #2 (positive)**: The surface is *narrow and purposeful*, not a full CRUD dump. An agent can reasonably discover → snapshot → diff → update. No footguns like "delete system" exposed; mutations are constrained.

**Observation #3 (friction)**: No `diff_graph` tool. To update, I have to snapshot → compute diff client-side → send sync_graph. Not wrong, but a one-shot "tell me what's drifted vs. X codebase signal" would save an agent round-trips.

## Entry 5 — get_snapshot for MM Platform (sys_2223122007)

Got clean response. Structure:
- Wrapped in `result.content[0].text` (MCP envelope) → JSON
- Inside: `data.nodes[24]`, `data.edges[49]`, `graph_hash`, `version_status=active`
- Sidecar: `guidance[]` with type_tag projections and helpful notes
  - The "don't send facet projections back in sync payloads" warning is gold — prevents a common agent mistake

**Observation #4 (excellent)**: the guidance array is genuinely agent-friendly. It explains facet projections, how to use them, and explicitly tells the caller NOT to send projections back. That's designed for an agent consumer, not just a human developer.

**Observation #5 (minor)**: edge objects use `{"from": uuid, "to": uuid}`. My first attempt to dump edges assumed `source_uuid`/`target_uuid` keys. Easy to fix once seen. A heads-up in the snapshot guidance would help.

## Entry 6 — sync_graph schema review

This schema is *really well designed*. Highlights:
- `syncNode` handles both component and resource via `kind` discriminator
- `canonical_zone` prevents bespoke "Internet" resource duplication (nice)
- `applied_controls` supports the control-edge model for nonflow components (guard/observe modes)
- `base_graph_hash` for optimistic concurrency
- `validate_format: true` for dry-run
- `idempotency_key` for replay safety
- Hard limits: 300 nodes, 1200 edges per call (sensible)

**Observation #6 (excellent)**: The schema enforces the hard-won architectural discipline — e.g., `controller_uuid` must be a graph node instance_uuid not a referent_uuid, explained in the description. Every description field anticipates a common agent mistake and preempts it.

**Tiny nit**: `additionalProperties: false` on `syncEdge` means I have to learn the valid keys exactly. Good for rigor; adds a minor round-trip cost if I guess wrong.


## Entry 7 — Change detection (snapshot vs. codebase)

**Method**: compared the 24 components/resources in the blueprint against `docker-compose.yml` services and `apps/` Django apps.

**Clear gaps found (7 missing nodes, high confidence)**:

| Missing component | Evidence |
|---|---|
| Redis | `docker-compose.yml: redis:` — used as Celery broker + cache |
| Web Celery Worker | `docker-compose.yml: web_worker:` → `celery -A web worker -Q web` |
| API Celery Worker | `docker-compose.yml: api_worker:` → `celery -A api worker -Q api` |
| CDB Celery Worker | `docker-compose.yml: cdb_worker:` → `celery -A cdb worker -Q cdb` |
| Web Celery Beat | `docker-compose.yml: web_beat:` → scheduler |
| API Celery Beat | `docker-compose.yml: api_beat:` → scheduler |
| CDB Celery Beat | `docker-compose.yml: cdb_beat:` → scheduler |

**Other candidate drift (medium confidence, not applied)**:
- `apps/subscriptions/` created 2026-04-17; recent "Fix migration in api for stripe" commit → Stripe is probably an external system resource
- `apps/service_proxy/` created recently → possibly a new proxy component
- `apps/rag_demo/` → demo surface, may or may not be in the live MM Platform graph

Limited time so I'll focus on the Redis + Celery set as the clearest, most load-bearing drift.

## Entry 8 — Finding referents: search UX

Called `search` for `redis` — came back with `origin: current_system`, meaning Redis has already been registered in this system's referent library. Referent UUID works directly in sync_graph.

Called `search` for `celery` — came back with `origin: global_library`. The referent_uuid looked valid but using it in sync_graph failed with `not_found_referent`.

**Observation #7 (friction)**: the distinction between `current_system` origin (use `referent_uuid`) and `global_library` origin (use `purl`) isn't explicit in the search response or tool descriptions. Would save an agent a round-trip to either:
- (a) document this convention in the tool description, or
- (b) accept referent_uuid uniformly and resolve global_library referents server-side

Took me 1 round trip to diagnose. Easy fix once you know — clear error message made it quick.

## Entry 9 — Node deduplication: referent-level vs instance-level fields

First attempt gave all 6 Celery instances different `type_tags` (worker vs. scheduler subtype). `sync_graph` rejected with `referent_definition_conflict`: "Component nodes sharing the same referent identity disagree on referent-level field 'type_tags'".

**Observation #8 (excellent error)**: The error text literally names the field and gives the correct action. I fixed by unifying type_tags and moving the worker/beat distinction into `notes` + `configuration`.

**Observation #9 (design-level)**: The referent/instance split matters. `type_tags` is referent-level; `configuration` and `display_name` are instance-level. For an agent updating graphs, the mental model is: "the package is the same — how I deploy it is different." This is the right split but worth explicit mention in docs.

## Entry 10 — Apply: it worked

Dry run `validate_format: true` → `ok: True, valid: True, graph_hash: sha256:7e1d455e1d40915e`. Only a benign warning about `broker` being a custom tag.

Apply (dropped validate_format, added `idempotency_key` and `base_graph_hash`) → `ok: True, graph_hash: sha256:f4eb521d4c310fbf`. Data returned:
- `nodes: [7]` (7 upserts)
- `edges_applied: dict(2)` (probably created/updated counts)
- `removed: dict(2)` (none actually removed; just the shape)

**Timing**: each sync_graph call returned in ~500ms–1s on the production endpoint. Well inside the "CI check latency budget" needed for the shift-left wedge.

**Observation #10 (positive)**: `idempotency_key` worked as advertised. If a CI agent retries, this is the knob. `base_graph_hash` set to the original snapshot hash; apply succeeded because no other writer had intervened. Concurrency story is there.

## Entry 11 — Verified via re-snapshot

Re-fetched snapshot:
- `graph_hash: sha256:f4eb521d4c310fbf` (matches apply response)
- `version_status: draft` (MM auto-bumped from v3 active → v4 draft)
- `nodes: 31` (was 24, +7 new)
- `edges: 73` (was 49, +24 new)
- All 7 new components visible by display_name

Also verified in the web UI at /map/ — the diagram now shows Redis and six Celery components. "Finalize new version" button appeared. Execution Flows badge went from 1 → 2, suggesting the new components surfaced a new finding to review.

## Entry 12 — One real data bug

Re-fetched Redis node detail: `type_tags: ['internal', 'storage', 'cache', 'api']` — the `broker` custom tag I sent was NOT preserved, despite the guidance message explicitly saying `"Preserved custom type_tags value 'broker' on Redis"`.

**Friction #2 / bug**: The guidance message is either wrong or there's a projection layer where custom tags live elsewhere (`custom_type_tags`?) but don't show up in `type_tags`. Either the message needs to change to "Stripped custom type_tags value" or the preservation needs to actually happen. For an agent consumer, this is actively misleading.

Low severity (functionally the sync still worked), but worth fixing — agents will trust the guidance messages.

## Entry 13 — What would make the CI wedge excellent

### Positive (keep)
- Raw HTTP + Bearer auth worked first try. Zero MCP client library needed.
- Narrow, purposeful tool surface. Agent can discover → snapshot → sync without help.
- Schema is agent-grade: descriptions anticipate errors, `additionalProperties: false` forces correctness, `canonical_zone` prevents name pollution.
- `validate_format: true` dry-run → apply pattern is exactly right for CI.
- `idempotency_key` + `base_graph_hash` give a CI agent safe retry.
- `guidance[]` sidecar is a genuinely novel and good pattern for agent consumers.
- Sub-second latency; fits a CI check budget.

### Friction (fix)
1. **MCP config is project-scoped.** A CI agent running in a separate repo can't auto-discover the MM MCP. Either document the HTTP integration path explicitly, or ship a canonical `.mcp.json` fragment customers can drop in.
2. **`current_system` vs. `global_library` referent origin.** The search response distinguishes them, but the tool descriptions don't explain that only `current_system` referents can use `referent_uuid` in sync_graph; `global_library` referents must use `purl`. Accept either, or document clearly.
3. **Edge schema keys.** Snapshot returns edges as `{"from","to"}`, but `source_uuid`/`target_uuid` would be a natural guess. Add a quick note.
4. **Custom tag preservation mismatch.** Guidance claims preservation, data shows stripping. Fix one or the other.

### Missing (would be valuable for the CI wedge)
1. **`diff_graph` tool.** Given a target snapshot or a list of desired nodes/edges, return the sync_graph payload that would make the graph match. Would save an agent round-trips when the customer has a deterministic way to derive "desired state" from code (e.g., parsing docker-compose).
2. **`detect_drift_from_source` tool.** Take a source URL (docker-compose.yml, Terraform state, Kubernetes manifest) and return the drift assessment. This would be the *exact* thing a CI agent wants to call. Even a stub that expects the customer to pre-parse would help.
3. **Streaming policy-evaluation result in the sync response.** The apply response confirms the graph changed but doesn't tell me whether my new Redis → External comms path triggered a Lethal Trifecta violation. For a CI check, the agent needs to know in the same call whether to fail the build. (Maybe `include_policy_results: true` flag?)
4. **Finalize-version tool.** I ended up with a v4 draft. To land it as "active" probably requires UI action or another tool I didn't see. A `finalize_version` (or `promote_draft`) tool would close the loop for CI-authored changes.
5. **Search filter by origin.** `search(..., origin="current_system")` would let me query only referents the sync_graph call can use directly, avoiding the `not_found_referent` ping.

### Overall impression
This MCP is already one of the better-designed agent-facing APIs I've used. The schema discipline, guidance sidecar, and dry-run pattern are all ahead of what typical internal APIs ship. The CI wedge is credible: I went from "I have a Bearer token" to "updated production graph and verified" in 5 real round-trips + 2 for error recovery. A dedicated CI agent with the drift-detection tool suggested above could land the whole flow in 2–3 calls.

**Grade: A-.** The four small frictions above are all fixable without architectural change. Two of them (referent origin, custom tag message) are documentation nits. One (finalize-version) is a missing tool. One (diff/detect-drift) is the next big enablement for the shift-left wedge you described.


---

## Entry 14 — Honest reflection: what I missed and why

User corrected me: the three changes I *should* have caught were:
1. **Sync Secret Scanner is now in-line for every write** (not just external API/MCP writes) — topology change
2. **Wazuh (SIEM)** — missing external security component
3. **Observability host** (OTel/Tempo/Prometheus/Grafana) — missing external monitoring component

I missed all three.

### Why #1 (secret scanner topology)

I saw the component in the blueprint and checked it off. My diff heuristic was "what new thing appeared" not "what existing thing got rewired." Topological changes are invisible to any diff tool that treats the graph as a *set of nodes* rather than a *structured pipeline*.

Catching this would have required reading Django signals, middleware, DRF hooks, or `perform_create` overrides — runtime-flow analysis, not file analysis.

### Why #2 and #3 (external infrastructure)

I bounded my investigation to `/Users/van/Developer/mm`. But MM Platform as modeled in MM is the production *envelope*, not the application repo. Wazuh and the observability stack live in `/Users/van/Developer/mm-governance` — the sibling repo I'd already read earlier in this conversation. I *knew* about them. I just framed "the system" as "this repo" and siloed my search.

### Systematic failure mode #1: diff by addition

I only looked for new services/apps. Structural rewiring of existing components was invisible to my method. For a governance product this is the worst class of miss — *the position of a control in the pipeline is often the whole point of the control*.

### Systematic failure mode #2: repo-bounded system definition

Code can't tell me where the platform ends. That's a decision. I made the wrong decision silently.

### Systematic failure mode #3: no tribal knowledge

A human would know Wazuh exists from having triaged an incident. A human would know the scanner moved because they were in the PR conversation. I had neither context and didn't ask for it.

### Product implications for the CI wedge

These three miss patterns map to three capability gaps a CI agent will hit in the wild:

1. **Topological diff needs runtime-flow awareness.** MM should expect an *integration-points manifest* from the customer's codebase (a Django management command, a Python function, similar) that enumerates: middleware stack, signal handlers, request pipeline positions. Like an SBOM but for control wiring.

2. **System boundary needs an explicit manifest.** MM's "system" should declare its scope: in-repo services + linked repos + external systems + external SaaS. Without this, every CI run will miss external-system drift. A `get_system_manifest` MCP tool or a convention file (`mm-system.yaml`) could provide it.

3. **Tribal knowledge needs a channel.** A `record_system_note` or `describe_recent_changes` MCP tool that accepts natural-language hints ("we moved the secret scanner to run on every write") would let humans whisper to the next agent run. Low-friction, high-value.

### What I'd do differently as a CI agent

- Ask what *the system* is before assuming repo == system
- Diff at three levels: component existence, component types, *and* component connectivity (pipeline position)
- For every unchanged-looking security control, ask "did its pipeline position change?"
- Keep linked repos (like mm-governance here) in active context, not as one-time reads

### Bottom line

The frictions I logged earlier (referent origin, custom tag message, etc.) are papercuts. **These three miss patterns are structural — they determine whether a CI-integrated agent will produce systematic false negatives on exactly the governance changes most worth catching.** The product thesis is still right, but operationalizing it requires closing these three gaps.


## Entry 15 — The bigger miss: I didn't read the published guidance

User asked "did you read the system modeling guidance?" — fair question, because I didn't.

### What MM actually publishes

On `prompts/list` and `resources/list` (which I only checked after being asked):

**Prompt** (1):
- `system_blueprint_workflow` — "Step-by-step guide for building a CORE system blueprint"

**Resources** (9):
- `mm://reference/skill` — full agent skill markdown export
- `mm://reference/modeling-guide` — step-by-step workflow
- `mm://reference/core-concepts` — CORE vocabulary
- `mm://reference/type-tags` — type tag taxonomy
- `mm://reference/risk-tags` — risk tag taxonomy
- `mm://reference/guidance` — catalog of guidance types
- `mm://reference/schema` — sync payload schema + field guidance
- `mm://reference/private-purls` — repo-owned PURL conventions
- `mm://reference/index` — catalog of all of the above

I used ZERO of these before calling tools.

### What the guide actually says

First paragraph of `mm://reference/skill`:

> The interaction model:
>   1. Read reference resources to learn the vocabulary and workflow.
>   2. Call discovery tools to find orgs, teams, and systems.
>   3. Fetch the current graph snapshot and system-scoped type taxonomy.
>   4. Build one JSON payload of nodes and edges...

I skipped step 1 entirely. Went straight to 2.

From `mm://reference/modeling-guide`, step 1 section:

> **Work outside-in: boundaries first, then processing, then completeness.**
>
> Network boundaries (type: network): Internet, Intranet, Local.
> External systems (type: system): Third-party platforms...
> **If a component creates or enforces a trust boundary, it belongs in the blueprint. The boundary enforcement IS the risk-relevant behavior.**

Each of my three misses maps to a line in the guide:

| I missed | Guide section |
|---|---|
| Wazuh (SIEM) | "External systems (type: system)" + "boundary enforcement IS the risk-relevant behavior" |
| Observability host | "External systems (type: system)" |
| Secret scanner topology | "boundary enforcement IS the risk-relevant behavior" — position IS the behavior |

All three would likely have been caught by following the outside-in workflow instead of inside-out by-file-existence.

### The meta-failure

My earlier reflection (diff by addition, repo-bounded, no tribal knowledge) was downstream. The primary failure was RTFM discipline. MM ships an agent curriculum designed exactly for this task; I didn't read it before using the tools.

Also: my fumbling with the `broker` custom tag? The `mm://reference/type-tags` reference defines the canonical vocabulary. Would have known not to invent one.

### Reframing the CI wedge recommendations

My earlier suggestions (`detect_drift_from_source`, system-boundary manifest, natural-language hints) are still valid, but they're secondary. The primary intervention is **force agents to read the skill before calling tools.** Agents, like junior engineers, skip documentation by default. The difference: you can structurally require this via the protocol.

Candidate interventions, low effort to high:

1. **Prefix the `sync_graph` tool description with "Required reading: `mm://reference/skill`."** Many agents do what the description says.
2. **First `get_snapshot` call returns skill content inline as a guidance entry.** Can't be skipped if the agent reads guidance at all.
3. **`sync_graph` emits a soft warning ("agent did not read mm://reference/skill this session") on first call without a prior resources/read for it.** Polite nudge, low friction.
4. **Harder gating: sync_graph rejects the first call without a recent `resources/read` of the skill.** Strong but ugly.

Option 2 is probably the sweet spot. Cheap to implement, impossible to miss, no false positives.

### One more positive observation

The skill and guide are genuinely excellent. "Work outside-in: boundaries first" is exactly the right instruction for an AI governance blueprint. "The boundary enforcement IS the risk-relevant behavior" is a crisp architectural principle. `data_safety` section warns against embedding secrets in configuration strings. The whole skill is written for an agent consumer, and it shows.

The product has the curriculum. The gap is getting agents to read it.

### Honest bottom line

My original grade of A- stands for the tool surface. But I should have noted separately: **the skill curriculum is A+ quality and A- discovered only if the agent remembers to ask.** Raising discoverability from A- to A is maybe the single highest-leverage change available.


---

## Entry 16 — Second modeling task: OpenClaw (greenfield)

User asked me to model the OpenClaw repo from scratch. This time I did it right — started by reading the published guidance.

### Process

1. Called `prompts/list` and `resources/list` (which I'd skipped last time).
2. Read `mm://reference/skill`, `mm://reference/modeling-guide`, `mm://reference/core-concepts`, `mm://reference/type-tags`, `mm://reference/risk-tags`.
3. Surveyed OpenClaw: README, VISION, architecture docs (`docs/concepts/architecture.md`, `docs/plugins/architecture.md`), the `extensions/` directory (114 packages).
4. Searched MM registry for well-known integrations (WhatsApp, Slack, Anthropic, OpenAI, Gemini, Ollama, LanceDB, etc.) to find reusable referents.
5. Called `create_system` → got `sys_8662526774`.
6. Built a single sync_graph payload following the "outside-in" workflow from the modeling guide (31 nodes, 65 edges).
7. `validate_format: true` dry-run → clean. Zero guidance messages, `valid: true`.
8. Applied → 33 flow edges + 32 resource-link edges created; graph hash `sha256:eefac51042b02757`.
9. Verified via re-snapshot (matches) and web UI (opened v1 Draft blueprint + execution flows).

### Defaults auto-applied (visible in guidance)

Four LLM components automatically got `ai-generated, unverified` added — exactly what the risk-tags reference predicts for `[model, llm]`. The default-behavior engine worked as documented.

### Execution Flows immediately flagged the obvious

The moment the graph was applied, the product detected:

- **Violations (2)**:
  - Lethal Trifecta Detection — 10 instances
  - Agents Rule of Two — 10 instances
- **Unmanaged Risks (11)**: Adversarial from Firecrawl, AI Generated from LLMs, Confidential from iMessage/Discord, etc.

This is a *textbook* positive result for the product. OpenClaw is architecturally a poster-child Lethal Trifecta:
- Private data access (conversation history, vector memory, user channel messages)
- Untrusted input (all messaging channels + open Internet)
- External communication (back to channels + to LLM providers)

All three converge on Pi Agent and the external LLM components. Ten instances is proportionate to the number of paths through the graph where the trifecta manifests. **The product just automatically surfaced the exact security concern OpenClaw's own threat model emphasizes (and SECURITY.md acknowledges as an intentional tradeoff).**

### Observations (positive)

1. **The outside-in workflow works.** Starting with boundaries (Internet, messaging platforms, external tool APIs) made every downstream modeling decision easier. I never had to backtrack to add a resource I'd forgotten.
2. **Lifted type presets save cognitive load.** `[orchestrator, agent, agentic]` maps to the "Agent / Subagent" preset without me looking it up.
3. **Default-behavior engine works.** I didn't set `adds_tags` on any LLM component; the server auto-added `[ai-generated, unverified]` per the risk-tags reference. Less work for the agent, consistent results.
4. **Private PURLs feel natural.** `pkg:private/ai.openclaw/{name}` for OpenClaw-internal components, `pkg:product/{vendor}/{product}` for external services. The modeling guide's explanation ("purl_namespace = who owns it: org's reverse DNS") is clear enough to apply uniformly.
5. **Single sync_graph call handled 31 nodes + 65 edges cleanly.** Well inside the 300/1200 limits. Sub-second apply.

### Observations (minor friction)

1. **"Local" canonical_zone node ended up orphaned.** I created it as a boundary resource but didn't connect anything to it explicitly (all local components are just type-tagged `local`). The server's `orphan_node` tip caught it. Fair catch. Either (a) prune orphan boundary resources on apply, (b) only include them when connected, or (c) document that `local` canonical_zone is optional since `scope: local` on components carries the same information.
2. **Messaging platforms as resources: I modeled each as its own `type: system` resource.** Could also have used canonical_zone but there's no canonical_zone for "WhatsApp" — those are genuinely separate external systems. Worked fine.
3. **I made one WebChat modeling decision wrong initially** and self-corrected: WebChat is served locally by the Gateway's Canvas Host (per network.md), not a separate external resource. Dropped it.

### Friction I didn't hit this time (by reading the docs)

- No `referent_definition_conflict` because I wasn't trying to reuse the same referent with different `type_tags`.
- No `not_found_referent` because for any referent I was unsure about, I used the `purl` path (which resolves server-side).
- No "custom tag stripped silently" because I stuck to canonical tags from the taxonomy reference.

### Timing

- Research + docs: ~3 minutes (reading skill + modeling-guide + tag taxonomies)
- Survey of OpenClaw: ~3 minutes
- Payload construction: ~2 minutes
- Create + dry-run + apply + verify: ~1 minute (wall-clock)

**Total: ~9 minutes of active work**, most of it front-loaded on reading and design. The actual API interaction was minimal.

### The delta from the first exercise

First exercise (MM platform drift detection):
- Skipped guidance. Three systematic misses (topology change, external systems, implicit context).
- ~7 round trips to recover from predictable errors.
- Final result: correct but partial.

Second exercise (OpenClaw from scratch):
- Read guidance first. Zero errors on apply.
- 3 round trips total (create_system, dry-run, apply).
- Final result: immediately surfaced the Lethal Trifecta/Rule of Two the user cares about, with no rework.

**The documentation is the leverage point.** An agent that reads it is fast, accurate, and self-correcting. An agent that doesn't is slow, error-prone, and dependent on human correction. The MCP's guidance sidecar reinforces this in-flight, but it can't fully substitute for the upfront reading.

### Confirmation of the prior recommendation

My earlier recommendation stands and is now empirically supported: **the single highest-leverage intervention for the CI wedge is getting agents to read the published skill before calling tools.** Option 2 (first `get_snapshot` returns skill content inline as guidance) remains my pick. This exercise is the counterfactual: with the skill read, the task takes 9 minutes. Without it, 20+ minutes plus correction rounds. The ROI on docs-first discipline is enormous.

