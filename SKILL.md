# Alph — Context Architect Skill

You have access to the **alph** MCP server. Alph is a git-backed context
engine that stores, retrieves, and validates structured context nodes across
registries and pools.

## Mental model

```
registry  "All household context"
  pool    "vehicles"       ← a scoped collection of nodes
    node  "Oil change..."  ← a fixed snapshot or live pointer
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
| `validate_pool` | Confirm all nodes are schema-compliant. Use after bulk adds. |

## Typical workflows

**Loading context for a question:**
1. `list_pool_nodes(pool_path=...)` — scan context fields
2. Identify relevant node IDs from the summaries
3. `show_pool_node(...)` for each relevant node — read full content
4. Answer the question from loaded content

**Capturing new context:**
1. `list_pool_nodes(...)` — quick check for duplicates
2. `add_node(pool_path=..., context=..., creator=...)` — create the node
3. For decisions or notes with body text, pass `content=` as Markdown

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

- `fixed` — snapshot: content is frozen at creation time, lives in `snapshots/`
- `live` — pointer: references an external resource (Jira, Google Doc, etc.),
  lives in `pointers/`. Full content must be fetched from the external system
  at query time using the `meta.provider` hint.

## Key fields

```yaml
context: str        # What this node is — read this first
node_type: fixed|live
status: active|archived|suppressed
tags: [list]        # Domain labels — for categorization, not filtering
related_to: [list]  # Cross-references: node_id, pool::node_id, registry::pool::node_id
meta: {}            # Source-specific data: url, doc_id, provider, resolves_to
```

## What alph is not

- Not a search engine — it doesn't do semantic search (yet). Scan `context`
  fields and use `show_pool_node` for targeted retrieval.
- Not a task manager — `tags: [open]` labels a node, but task tracking
  belongs in your task system. Use live nodes to point at Jira tickets.
- Not a database — it's git-backed Markdown. Keep nodes focused and human-readable.

## Install this skill

```bash
mkdir -p ~/.claude/skills/alph
cp SKILL.md ~/.claude/skills/alph/SKILL.md
```
