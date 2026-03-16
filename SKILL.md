# Alph — Context Architect Skill

You have access to the **alph** MCP server. Alph is a git-backed context
engine that stores, retrieves, and validates structured context nodes across
registries and pools.

## Mental model

```
registry  "All household context"
  pool    "vehicles"       <- a scoped collection of nodes
    node  "Oil change..."  <- a fixed snapshot or live pointer
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
| `validate_pool` | Confirm all nodes are schema-compliant. Use after bulk adds. |

## Hydration — resolving live nodes

Live nodes point at external resources (Google Docs, Confluence pages, Jira
tickets, Slack channels). When `show_pool_node` returns a non-empty
`hydration_instructions` field, follow those instructions to fetch the
current content using the indicated MCP server or provider.

**Workflow:**
1. `show_pool_node(...)` — read the node
2. If `hydration_instructions` is present, follow them (they name the MCP
   server and explain which meta fields to use)
3. If `hydration_instructions` is empty, fall back to generic patterns:
   - `meta.url` — try fetching the URL directly
   - `content_type: gdoc` — use a Google Docs MCP server with `meta.url`
   - `content_type: confluence` — use an Atlassian MCP server with `meta.url`
   - `content_type: jira` — use an Atlassian MCP server with `meta.issue_key`
   - `content_type: slack` — use a Slack MCP server with `meta.channel`
     (and `meta.thread_ts` if present)

Hydration is registry-scoped: the same content type may resolve differently
across registries (different auth, workspace, MCP servers). The instructions
come from `hydration.yaml` at the registry root.

## Typical workflows

**Loading context for a question:**
1. `list_pool_nodes(pool_path=...)` — scan context fields
2. Identify relevant node IDs from the summaries
3. `show_pool_node(...)` for each relevant node — read full content
4. For live nodes with `hydration_instructions`, follow them to fetch current content
5. Answer the question from loaded content

**Capturing new context:**
1. `list_pool_nodes(...)` — quick check for duplicates
2. `add_node(pool_path=..., context=..., creator=...)` — create the node
3. For decisions or notes with body text, pass `content=` as Markdown
4. For live pointers, set `node_type="live"`, `content_type`, and relevant `meta`

**After bulk operations:**
1. `validate_pool(pool_path=...)` — confirm schema compliance

## Status and filtering

Nodes have a `status` field:
- `active` — default, included in all queries
- `archived` — historical record, excluded by default
- `suppressed` — still relevant but verbose, excluded by default

To include non-active nodes: `list_pool_nodes(..., include_statuses=["archived"])`
or `include_statuses=["all"]` to see everything.

## Node types

- `snapshot` (alias: `snap`) — content is frozen at creation time, lives in `snapshots/`
- `live` — pointer to an external resource (Jira, Google Doc, Slack, etc.),
  lives in `live/`. Content must be fetched from the external system at query
  time using hydration instructions or meta fields.

## Content types

Built-in: `text`, `gdoc`, `slack`, `jira`, `confluence`, `email`, `image`,
`figma`, `task`. Registries can declare additional custom types via
`hydration.yaml`.

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

## What alph is not

- Not a search engine — scan `context` fields and use `show_pool_node` for
  targeted retrieval.
- Not a task manager — use live nodes to point at Jira tickets or task systems.
- Not a database — it's git-backed Markdown. Keep nodes focused and human-readable.

## Install this skill

```bash
mkdir -p ~/.claude/skills/alph
cp SKILL.md ~/.claude/skills/alph/SKILL.md
```
