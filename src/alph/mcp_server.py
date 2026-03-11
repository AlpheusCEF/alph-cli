"""AlpheusCEF MCP server — FastMCP wrapper exposing core.py as MCP tools.

Start the server:
    python -m alph.mcp_server

Or via the installed entry point:
    alph-mcp

Claude Code configuration (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "alph": {
          "command": "alph-mcp"
        }
      }
    }

All tools follow the Basic Memory pattern:
- One tool per operation
- Detailed docstrings so Claude understands when and how to use each tool
- Dual output: structured dict returned by the Python function; FastMCP
  serialises it as JSON for the MCP protocol layer
- MCP annotations declare read/write intent and idempotency
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import fastmcp
from mcp.types import ToolAnnotations

from alph.core import (
    create_node,
    extract_frontmatter,
    is_remote_registry,
    list_nodes,
    parse_remote_registry,
    show_node,
    validate_node,
)
from alph.remote import provider_for_url, resolve_pool_readonly

mcp = fastmcp.FastMCP(
    name="alph",
    instructions=(
        "Alpheus Context Engine Framework. Use these tools to create, list, "
        "show, and validate context nodes in a pool. "
        "A pool is a directory containing snapshots/ (snapshot nodes) and "
        "live/ (live nodes). "
        "Always use tool_list_nodes to discover existing nodes before adding "
        "new ones — alph deduplicates by ID but a quick scan avoids surprises. "
        "Use tool_show_node to read full content including body text. "
        "Use tool_validate_pool to confirm pool health after bulk operations."
    ),
)


# ---------------------------------------------------------------------------
# Remote pool resolution
# ---------------------------------------------------------------------------


@contextmanager
def _resolve_pool(pool_path: str) -> Iterator[Path]:
    """Resolve pool_path to a local Path, fetching remotely if needed."""
    if is_remote_registry(pool_path):
        ref = parse_remote_registry(pool_path)
        provider = provider_for_url(ref.remote_url)
        with resolve_pool_readonly(provider, ref.subpath) as local:
            yield local
    else:
        yield Path(pool_path)


# ---------------------------------------------------------------------------
# Public tool functions (also importable for testing without MCP runtime)
# ---------------------------------------------------------------------------


def tool_add_node(
    *,
    pool_path: str,
    context: str,
    creator: str,
    node_type: str = "snapshot",
    content: str = "",
    status: str | None = None,
    tags: list[str] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Create a context node and return its ID and path.

    Args:
        pool_path: Absolute path to the pool directory.
        context: Human/LLM-readable description of this node. This is the
            primary field — make it specific enough to be useful in a list
            scan without reading the full body.
        creator: Email address of the person or system creating this node.
        node_type: 'snapshot' (or 'snap') default, or 'live' for a resource that changes over time.
        content: Optional Markdown body text below the frontmatter.
        status: 'active' (default/omit), 'archived' (historical, excluded
            from default queries), or 'suppressed' (relevant but verbose,
            excluded from default queries).
        tags: Optional list of semantic labels (e.g. ['repair', 'crv']).
        timestamp: ISO-8601 timestamp. Defaults to now (UTC). Set explicitly
            for backdated or imported nodes.

    Returns:
        dict with keys:
            status: 'created' or 'duplicate'
            node_id: 12-char ID
            node_type: 'snapshot' or 'live'
            path: absolute path to the written file (snapshots/ or live/)
            node_status: the status value written to frontmatter (if set)
            existing_creator: set when status is 'duplicate'
    """
    if is_remote_registry(pool_path):
        return {
            "status": "error",
            "message": (
                "Write operations on remote pools are not supported in RO mode. "
                "Use a local pool path or set mode: rw in config."
            ),
        }
    result = create_node(
        pool_path=Path(pool_path),
        source="mcp",
        node_type=node_type,
        context=context,
        creator=creator,
        content=content,
        status=status,
        tags=tags or [],
        timestamp=timestamp,
    )
    if result.duplicate:
        return {
            "status": "duplicate",
            "node_id": result.node_id,
            "node_type": node_type,
            "existing_creator": result.existing_creator,
        }
    response: dict[str, Any] = {
        "status": "created",
        "node_id": result.node_id,
        "node_type": node_type,
        "path": str(result.path),
    }
    if status is not None:
        response["node_status"] = status
    return response


def tool_list_nodes(
    *,
    pool_path: str,
    include_statuses: list[str] | None = None,
) -> dict[str, Any]:
    """List nodes in a pool as lightweight summaries.

    Returns active nodes by default. Use include_statuses to expand the
    result set — useful when reviewing archived decisions or suppressed
    verbose context.

    Args:
        pool_path: Absolute path to the pool directory.
        include_statuses: Additional statuses to include alongside active.
            Values: 'archived', 'suppressed', or 'all' (include everything).
            Omit or pass empty list for active-only (default).

    Returns:
        dict with keys:
            count: total number of nodes returned
            nodes: list of node summaries, each with:
                node_id, context, node_type, status, timestamp, source
    """
    if not include_statuses:
        statuses: set[str] = {"active"}
    elif "all" in include_statuses:
        statuses = {"active", "archived", "suppressed"}
    else:
        statuses = {"active"} | set(include_statuses)

    with _resolve_pool(pool_path) as pool:
        summaries = list_nodes(pool, include_statuses=statuses)
    return {
        "count": len(summaries),
        "nodes": [
            {
                "node_id": s.node_id,
                "context": s.context,
                "node_type": s.node_type,
                "status": s.status,
                "timestamp": s.timestamp,
                "source": s.source,
            }
            for s in summaries
        ],
    }


def tool_show_node(
    *,
    pool_path: str,
    node_id: str,
) -> dict[str, Any]:
    """Show the full content of a node by ID.

    Use this after tool_list_nodes to read the body text and all metadata
    fields of a specific node.

    Args:
        pool_path: Absolute path to the pool directory.
        node_id: 12-character node ID (from tool_list_nodes output).

    Returns:
        dict with keys:
            found: True if the node exists, False otherwise
            node: full node detail (when found=True), with keys:
                node_id, context, node_type, status, timestamp, source,
                creator, body, tags, related_to, meta
    """
    with _resolve_pool(pool_path) as pool:
        detail = show_node(pool, node_id)
    if detail is None:
        return {"found": False}
    return {
        "found": True,
        "node": {
            "node_id": detail.node_id,
            "context": detail.context,
            "node_type": detail.node_type,
            "timestamp": detail.timestamp,
            "source": detail.source,
            "creator": detail.creator,
            "body": detail.body,
            "tags": detail.tags,
            "related_to": detail.related_to,
            "meta": detail.meta,
        },
    }


def tool_validate_pool(
    *,
    pool_path: str,
) -> dict[str, Any]:
    """Validate all nodes in a pool against the v1 schema.

    Use after bulk imports or before committing a pool to confirm all nodes
    are schema-compliant. Reports each invalid node and its specific errors.

    Args:
        pool_path: Absolute path to the pool directory.

    Returns:
        dict with keys:
            valid: True if all nodes pass validation
            error_count: total number of validation errors found
            errors: list of dicts, each with 'file' and 'errors' keys
    """
    all_errors: list[dict[str, Any]] = []

    with _resolve_pool(pool_path) as pool:
        for subdir in ("snapshots", "live"):
            directory = pool / subdir
            if not directory.exists():
                continue
            for node_file in sorted(directory.glob("*.md")):
                frontmatter = extract_frontmatter(node_file.read_text())
                if frontmatter is None:
                    all_errors.append({
                        "file": node_file.name,
                        "errors": ["no frontmatter found"],
                    })
                    continue
                result = validate_node(frontmatter)
                if not result.valid:
                    all_errors.append({
                        "file": node_file.name,
                        "errors": result.errors,
                    })

    return {
        "valid": len(all_errors) == 0,
        "error_count": sum(len(e["errors"]) for e in all_errors),
        "errors": all_errors,
    }


# ---------------------------------------------------------------------------
# MCP tool registrations
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=ToolAnnotations(
        title="Add context node",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def add_node(
    pool_path: str,
    context: str,
    creator: str,
    node_type: str = "snapshot",
    content: str = "",
    status: str | None = None,
    tags: list[str] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Create a context node in a pool. See tool_add_node for full docs."""
    return tool_add_node(
        pool_path=pool_path,
        context=context,
        creator=creator,
        node_type=node_type,
        content=content,
        status=status,
        tags=tags,
        timestamp=timestamp,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="List context nodes",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def list_pool_nodes(
    pool_path: str,
    include_statuses: list[str] | None = None,
) -> dict[str, Any]:
    """List nodes in a pool with lightweight summaries. See tool_list_nodes for full docs."""
    return tool_list_nodes(pool_path=pool_path, include_statuses=include_statuses)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Show context node",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def show_pool_node(
    pool_path: str,
    node_id: str,
) -> dict[str, Any]:
    """Show full content of a node by ID. See tool_show_node for full docs."""
    return tool_show_node(pool_path=pool_path, node_id=node_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Validate pool",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def validate_pool(
    pool_path: str,
) -> dict[str, Any]:
    """Validate all nodes in a pool against the schema. See tool_validate_pool for full docs."""
    return tool_validate_pool(pool_path=pool_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("alph-mcp: AlpheusCEF MCP server (stdio transport)")
        print("Usage: configure your MCP client to run 'alph-mcp' as a subprocess.")
        print("       Do not run directly — it speaks MCP protocol over stdin/stdout.")
        sys.exit(0)
    mcp.run()


if __name__ == "__main__":
    main()
