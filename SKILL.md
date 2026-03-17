# Alph — Context Architect Skill

You have access to the **alph** MCP server and CLI. Alph is a git-backed context
engine that stores, retrieves, and validates structured context nodes across
registries and pools.

## Mental model

```
registry  "All household context"
  pool    "vehicles"       <- a scoped collection of nodes
    node  "Oil change..."  <- a fixed snapshot or live pointer
    barrel/                <- cached hydrated content (gitignored)
  pool    "appliances"
  pool    "remodeling"
```

Every node has a `context` field — a human/LLM-readable description you scan
to decide relevance before loading full content. Read `context` first, then
use `show_pool_node` only for nodes that are relevant to the query.

## Tools

| Tool | When to use |
|------|-------------|
| `list_pool_nodes` | Discover what exists in a pool. Always do this before adding. |
| `show_pool_node` | Read full content + body of a specific node by ID. |
| `add_node` | Create a new context node. Idempotent — safe to retry. |
| `update_pool_node` | Modify an existing node (status, tags, meta, body). |
| `search_pool_nodes` | Search node frontmatter and body text by keyword. |
| `search_pool_barrel` | Search barrel cached content by keyword (deep search). |
| `validate_pool` | Confirm all nodes are schema-compliant. Use after bulk adds. |

For barrel cache management, use the `alph barrel` CLI (`alph b`):
`check`, `write`, `status`, `flush`, `invalidate`, `new`, `mark-read`, `export`.

## Hydration — resolving live nodes

Live nodes point at external resources (Google Docs, Confluence pages, Jira
tickets, Slack channels). Resolution is registry-scoped — the same content
type may resolve differently across registries (different auth, workspace,
MCP servers).

### Resolution workflow

1. **Read `hydration.yaml`** at the registry root. It defines:
   - `types` — how to resolve each content_type (provider, instructions)
   - `barrel` — cache policy (TTLs, fetch modes)
   - `context_queries` — how to synthesize answers for different question types
2. **Check the barrel first** (see Barrel section below).
3. If not cached or stale, follow the `hydration_instructions` from
   `show_pool_node` or the `types` instructions in `hydration.yaml`.
4. After fetching, cache the result in the barrel.

### Fallback patterns (when no hydration.yaml exists)

- `meta.url` — try fetching the URL directly
- `content_type: gdoc` — use a Google Docs MCP server with `meta.url`
- `content_type: confluence` — use an Atlassian MCP server with `meta.url`
- `content_type: jira` — use an Atlassian MCP server with `meta.issue_key`
- `content_type: slack` — use a Slack MCP server with `meta.channel`

## Barrel — hydration cache

The barrel is a per-pool cache of hydrated live node content. **Barrel
caching is always on by default.** Every time you hydrate a live node,
cache the result. Every time you need content, check the barrel first.

Use the `alph barrel` CLI (aliases: `alph bar`, `alph b`) for all cache
operations. Never manually write barrel files — the CLI ensures consistent
frontmatter.

### Defaults (no barrel config in hydration.yaml)

When a registry has no `barrel:` section, these defaults apply:

- **default_ttl**: 4h — all content types expire after 4 hours
- **fetch_mode**: full — always replace entire cache on refresh
- **Snapshots**: Cache on first read, never re-fetch (content is inline)

Registries can override per type in `hydration.yaml → barrel → types`.

### Barrel CLI commands

```bash
# Check if a cached entry is fresh, stale, or missing
alph b check <node_id> --pool <pool_path>

# Cache hydrated content after fetching
alph b write <node_id> --ct <content_type> --file <path> --pool <pool_path>

# Show cache status for all entries in a pool
alph b status --pool <pool_path>

# What's changed since last read
alph b new --pool <pool_path>

# Mark all entries as read (update timeline cursor)
alph b mark-read --pool <pool_path>

# Remove a specific cache entry (forces re-fetch next time)
alph b invalidate <node_id> --pool <pool_path>

# Remove all cache entries in a pool
alph b flush --pool <pool_path>

# Export all cached content
alph b export --pool <pool_path> --format md|json|yaml
```

### Hydration workflow with barrel

For each node being hydrated:

1. Run `alph b check <node_id>` to see if it's fresh, stale, or missing.
2. **Fresh**: Read the barrel file directly (`<pool>/barrel/<node_id>.md`).
   Do not re-fetch.
3. **Stale or missing**: Fetch content using `hydration.yaml -> types`
   instructions (MCP server, CLI tool, etc.). Write fetched content to a
   temp file, then cache with
   `alph b write <node_id> --ct <type> --file <temp_file>`.
4. After all nodes are resolved, run `alph b mark-read` to update the
   timeline cursor.

### Transparency

Always tell the user what the barrel is doing. Include a cache status line:

    Barrel: 5/6 nodes from cache (fresh), 1/6 re-fetched (slack, stale)

The user can manage the barrel directly:
- **"barrel status"** — run `alph b status`
- **"barrel refresh"** — run `alph b flush`, then re-hydrate all nodes
- **"barrel new"** / **"what's changed"** — run `alph b new`

## Context queries — synthesizing answers

Registries can define `context_queries` in `hydration.yaml` to guide how
you synthesize answers from hydrated content. Each query has:
- `matches` — natural language patterns to match against the user's question
- `instructions` — how to shape the response

When the user asks a high-level question about a pool:
1. List all nodes in the pool.
2. Hydrate every node (barrel-first).
3. Match the question against `context_queries -> matches`.
4. Follow the matching query's `instructions` to synthesize.
5. If no query matches, use judgment but still hydrate all nodes first.

## Temporal reasoning

Content from live sources represents a history of evolving decisions, not a
flat set of facts. When multiple statements address the same topic, **later
content supersedes earlier content**. Track the arc of decisions and always
be clear about what is current vs. historical.

## Live-to-snapshot signals

During hydration, watch for signals that a live node should be frozen
into a snapshot — its content has reached a final state and the live
pointer is no longer needed:

- **Jira ticket**: Status is Done, Closed, Resolved, or Won't Do
- **Design doc / PRD**: Marked "Approved", "Final", or "Archived"
- **Slack thread**: Discussion concluded with a clear decision
- **Any resource**: Deleted, moved, or returning 404/403 on hydration

When you detect this, tell the user:
1. Which node and why (e.g., "INFOSEC-5107 is Done — this live pointer
   can be frozen")
2. Suggest freezing: capture the current hydrated content as the node
   body and convert from live to snapshot

Note: `alph` does not yet have a `freeze` command. For now, the user
would need to manually move the file from `live/` to `snapshots/` and
add the hydrated content as body text. A proper `alph freeze <id>`
command is planned.

## Hydration failures

After attempting to hydrate all nodes, if **any** node failed (MCP server
unavailable, permission denied, etc.), **stop before synthesizing** and:

1. Show a table of all nodes with hydration status (success/failure/cached).
2. Warn that proceeding with partial context risks incomplete guidance.
3. Ask the user whether to proceed or stop.

Do not silently skip failed nodes and present a synthesis as complete.

## Typical workflows

**Answering a question about a project:**
1. Identify the pool from the user's query.
2. `list_pool_nodes(pool_path=...)` — scan context fields.
3. Hydrate all nodes using barrel workflow above.
4. Match against `context_queries` and synthesize.
5. Cite sources by node ID.

**Capturing new context:**
1. `list_pool_nodes(...)` — quick check for duplicates.
2. `add_node(pool_path=..., context=..., creator=...)` — create the node.
3. For decisions or notes with body text, pass `content=` as Markdown.
4. For live pointers, set `node_type="live"`, `content_type`, and relevant `meta`.

**After bulk operations:**
1. `validate_pool(pool_path=...)` — confirm schema compliance.

## Status and filtering

Nodes have a `status` field:
- `active` — default, included in all queries
- `archived` — historical record, excluded by default
- `suppressed` — still relevant but verbose, excluded by default

To include non-active nodes: `list_pool_nodes(..., include_statuses=["archived"])`
or `include_statuses=["all"]` to see everything.

## Node types

- `snapshot` (alias: `snap`) — content is frozen at creation time, lives in `snapshots/`
- `live` — pointer to an external resource, lives in `live/`. Content must be
  fetched at query time using hydration instructions.

## Content types

Built-in types with their required meta fields:

| Type | Required meta | Notes |
|------|--------------|-------|
| `text` | — | Default. Plain content, no external resource. |
| `gdoc` | `url` | Google Doc. |
| `confluence` | `url` | Confluence page. |
| `jira` | `url`, `issue_key` | Jira ticket. |
| `slack` | `url` OR `channel` | Slack channel or thread. `thread_ts` optional. |
| `email` | `from`, `subject` | Email message. |
| `image` | `url` | Image reference. |
| `figma` | `url` | Figma design file. |
| `task` | — | Flexible. Used by fin-cli for task management. |

Validation enforces required meta at node creation and update. Registries
can declare additional custom types via `hydration.yaml` — custom types
skip meta validation (the registry author defines requirements via
instructions).

## Key fields

```yaml
context: str              # What this node is — read this first
node_type: snapshot|live
content_type: text|gdoc|...
status: active|archived|suppressed
tags: [list]              # Domain labels for categorization
related_to: [list]        # Cross-references to other node IDs
meta: {}                  # Source-specific: url, issue_key, channel, etc.
```

## Search — finding content

Two search tiers:

```bash
# Shallow: search node frontmatter (context, tags, meta) and body text
alph search "oauth" --pool <pool_path>

# Deep: search barrel cached content (hydrated live content)
alph b search "PKCE" --pool <pool_path>
```

`alph search` answers "do I have a node about X?" — always available.
`alph b search` answers "does any source material mention X?" — only
works on content that's been hydrated into the barrel.

Both are case-insensitive and return node IDs with context and matching
excerpts.

## What alph is not

- Not a task manager — use live nodes to point at Jira tickets or task systems.
- Not a database — it's git-backed Markdown. Keep nodes focused and human-readable.
