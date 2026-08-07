# Using Gristle from Claude Code

Hand this file to a Claude Code session (or any coding agent) to set up and use
Gristle's code graph. Everything here is executable as written.

Gristle parses a repository into a FalkorDB graph — functions, calls, routes,
models, tests, feature flags — and serves it over MCP.

---

## 1. Is it already set up?

```bash
gristle doctor        # checks FalkorDB, parsers, config, lists graphs
gristle repos         # every indexed graph: node count, freshness, source path
```

If `gristle doctor` reports all checks passed and `gristle repos` lists this
repo, skip to section 4.

If `gristle doctor` prints `Starting Gristle MCP server` instead of running
doctor, see **Trap 1** below — the CLI is broken and must be fixed first.

## 2. First-time setup on a new machine

**Start the database.** FalkorDB must be running before any tool call.

```bash
docker compose up -d falkordb     # from the gristle repo
gristle doctor                    # confirm it's reachable
```

**Register the MCP server**, user scope so every project gets it:

```bash
claude mcp add gristle -s user \
  -e GRISTLE_FALKORDB_HOST=localhost \
  -e GRISTLE_FALKORDB_PORT=6390 \
  -- gristle serve
```

On Windows, or wherever the console script isn't on `PATH`, use its absolute
path in place of `gristle` (find it with `python -c "import sys,os;
print(os.path.join(sys.prefix,'Scripts'))"` or `which gristle`).

Verify:

```bash
claude mcp get gristle            # expect: Status: connected
```

**Allow the tools** so each call doesn't prompt. In `~/.claude/settings.json`,
under `permissions`:

```json
{
  "permissions": {
    "allow": ["mcp__gristle"],
    "deny":  ["mcp__gristle__gristle_drop"]
  }
}
```

`gristle_drop` deletes a graph, so it stays behind a prompt.

**Restart the session.** MCP servers and skills are read once, at session start.
A session that was already running cannot see them.

## 3. Index the repo

```bash
gristle ingest /absolute/path/to/repo --repo-id <short-slug>
```

Takes a minute or two on a large repo. Idempotent — re-run with the same
`repo_id` to refresh.

## 4. Using it

**Always pass `repo_id` explicitly on every call.** Omitting it falls back to
"the last repo ingested *in this server process*", which in a fresh session is
usually nothing — or worse, a different repo's graph.

| Question | Tool |
|---|---|
| What breaks if I change this? | `gristle_impact_score` |
| Who calls this, and where does it live? | `gristle_explore` |
| Which tests cover it? | `gristle_tests` |
| How does A reach B? | `gristle_trace` |
| Blast radius of an edit I'm about to make | `gristle_change_impact`, `gristle_changeset_impact` |
| What routes exist / which lack auth? | `gristle_routes`, `gristle_unauthenticated_routes` |
| What tables exist, with columns and keys? | `gristle_models`, `gristle_model_detail` |
| What patterns does this project follow? | `gristle_conventions` |
| Find an entity by fuzzy name | `gristle_search` |
| Unused exports, import cycles, public API | `gristle_dead_exports`, `gristle_cycles`, `gristle_public_api` |
| Feature flags: retire candidates, what each gates | `gristle_flag_analysis` |
| A diagram of one slice of the graph | `gristle_subgraph` |

**The habit that makes this worth it:** before editing any function, route, model
or component that isn't obviously local, call `gristle_impact_score` and
`gristle_tests` on it. Report the blast radius *before* writing the diff.

## 5. Keeping the graph current

The graph is a snapshot taken at ingest. Your own edits make it stale for the
files you touched, and impact answers from a stale graph are wrong in ways that
look right.

- `gristle_watch(action="start", repo_id=...)` auto-updates on file change — but
  only works if **you ingested in this same session**. A graph rehydrated from a
  previous session has no watcher attached; re-ingest first.
- Otherwise re-ingest after substantial changes.
- **While iterating on Gristle's own code, re-ingest from the CLI, not the
  `gristle_ingest` MCP tool.** The CLI spawns a fresh process and picks up
  working-tree changes immediately; `gristle serve` is long-lived and holds the
  code it loaded at session start, so ingesting through MCP silently writes a
  graph using the *previous* version.

## 6. Trust boundary — state this honestly

Gristle resolves calls and imports by **name and heuristic**, not by type. High
coverage and excellent for navigation and architecture, but edges are
**best-effort, not proofs**:

- A missing edge does not prove nothing calls it. Dynamic dispatch, fully
  dynamic string keys, and reflection are invisible.
- Before *deleting* something Gristle calls dead, or asserting a path is
  unreachable, confirm by reading the code.
- It is not a dataflow/taint engine. `gristle_security` is a first-pass
  structural surface scan, not proof of exploitability.

Use it to know *where to look* and *what's connected*. Read the file to confirm
the specific claim.

---

## Traps

Both fail silently, which is what makes them worth writing down.

### Trap 1 — the console script goes stale

An editable install (`pip install -e .`) re-links the *package* on every change
but **never regenerates the command shim**. If the entry point in
`pyproject.toml` ever changed, the installed `gristle` still calls the old one
and discards your arguments.

**Symptom:** `gristle doctor` prints `Starting Gristle MCP server` instead of
running doctor. Every subcommand is affected.

```bash
python -m pip install -e /path/to/gristle --force-reinstall --no-deps
```

### Trap 2 — the FalkorDB volume mounted at the wrong path

FalkorDB's configured `dir` is `/var/lib/falkordb/data`, **not** the `/data`
that the plain Redis image uses. A compose file mounting the volume at `/data`
leaves `dump.rdb` on the container's writable layer — where a plain
`docker compose down` destroys it, no `-v` required.

```bash
# the data dir must be on a device, not "overlay"
docker exec <container> sh -c "cat /proc/mounts | grep falkordb"
```

Correct mount:

```yaml
volumes:
  - falkordb_data:/var/lib/falkordb/data
```

**Restoring a backup has an order that matters.** Redis writes a save on
shutdown, so copying a dump into a *running* container and then restarting
overwrites your copy with the live dataset. Stop first:

```bash
docker stop <container>
docker cp dump.rdb <container>:/var/lib/falkordb/data/dump.rdb
docker start <container>
```

---

Graphs are derived entirely from source. The worst case is re-ingesting, never
lost work.
