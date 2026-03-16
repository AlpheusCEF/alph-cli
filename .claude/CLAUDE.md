@/Users/cpettet/git/chasemp/AlpheusCEF/agents/CLAUDE.md

# alph-cli: Surface Area Checklist

When adding or changing functionality in core.py or cli.py, check whether these surfaces need updating too:

## MCP server (mcp_server.py)
- **New core function?** → Expose it as an MCP tool if Claude should be able to call it directly (read/query operations especially).
- **Changed function signature?** → Update the corresponding MCP tool wrapper and its docstring.
- **New capability area?** → Update the `instructions` string in the FastMCP constructor so Claude knows the capability exists.

## SKILL.md
- **New MCP tool?** → Add it to the Tools table in SKILL.md.
- **New CLI command?** → Add it to the relevant section (barrel, search, etc.).
- **Changed behavior?** → Update the workflow descriptions.
- SKILL.md is installed via `alph skill install` (symlink to brew share/). Changes propagate automatically on brew upgrade.

## human_test.sh
- **New command or subcommand?** → Add a section to human_test.sh covering the happy path.
- **Changed behavior?** → Update existing section assertions.

## Overview docs (separate repo)
- **Version bump?** → Update STATE.md (version, test count, feature list) and planned.md (version, test count).
- **New feature shipped?** → Add to planned.md "What shipped" and update plans.md suggested sequence.
