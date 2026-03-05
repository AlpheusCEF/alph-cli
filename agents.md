# Agent Setup

All AlpheusCEF repos share a single set of Claude Code agents and instructions maintained in the [agents repo](../agents/README.md).

## How it's wired in this repo

```
.claude/
  CLAUDE.md            # @/path/to/agents/CLAUDE.md — one-line import
  agents/
    tdd-guardian.md    # symlink → agents repo
    py-enforcer.md     # symlink → agents repo
    ...                # all agents are symlinks
  commands/
    pr.md              # symlink → agents repo
    ...
  settings.local.json  # per-repo permission allowlist, not shared
```

## Updating an agent

Edit the source file in the [agents repo](../agents/). Changes are immediately reflected here via symlinks — no copy or sync step.

## Adding a new repo to the ecosystem

See [agents/agents.md](../agents/agents.md) for the wiring instructions.

## Project context

- [overview/STATE.md](../overview/STATE.md) — full design and current status
- [overview/PLAN.md](../overview/PLAN.md) — phased implementation plan
- [overview/FUTURE.md](../overview/FUTURE.md) — future horizons and open questions
