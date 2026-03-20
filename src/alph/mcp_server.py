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

import importlib.metadata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import fastmcp
from mcp.types import ToolAnnotations

from alph.core import (
    LATEST_FILENAME,
    LATEST_NODE_ID,
    HydrationConfig,
    create_node,
    extract_frontmatter,
    find_registry_for_pool,
    is_remote_registry,
    list_nodes,
    load_config,
    load_hydration_config,
    parse_remote_registry,
    search_barrel,
    search_nodes,
    show_node,
    update_node,
    validate_node,
)
from alph.remote import provider_for_url, resolve_pool_readonly

_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "alph"

_MCP_SOURCE = f"alph-mcp/v{importlib.metadata.version('alph-cli')}"

mcp = fastmcp.FastMCP(
    name="alph",
    instructions=(
        "Alpheus Context Engine Framework. Use these tools to create, list, "
        "show, search, and validate context nodes in a pool. "
        "A pool is a directory containing snapshots/ (snapshot nodes), "
        "live/ (live nodes), and a _latest.md entry-point node at the pool root. "
        "Start by reading _latest (tool_show_node with node_id='_latest') for "
        "an overview of what matters in the pool before diving into individual nodes. "
        "Always use tool_list_nodes to discover existing nodes before adding "
        "new ones — alph deduplicates by ID but a quick scan avoids surprises. "
        "Use tool_show_node to read full content including body text. "
        "When show_pool_node returns hydration_instructions, follow those "
        "instructions to resolve the live node content using the indicated "
        "MCP server or provider. "
        "Use search_pool_nodes to find nodes by keyword across frontmatter and body. "
        "Use search_pool_barrel to search cached hydrated content (deep search). "
        "Use tool_validate_pool to confirm pool health after bulk operations. "
        "For hydration caching, use the alph barrel CLI (alph b check/write/status/flush). "
        "Read hydration.yaml at the registry root for resolution config, barrel TTLs, "
        "and context_queries synthesis patterns."
    ),
)


# ---------------------------------------------------------------------------
# Remote pool resolution
# ---------------------------------------------------------------------------


def _load_hydration_for_pool(pool_path: str, config_dir: str | None = None) -> HydrationConfig | None:
    """Load hydration config for a pool by finding its owning registry."""
    if is_remote_registry(pool_path):
        return None
    cfg_dir = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    cfg = load_config(global_config_dir=cfg_dir)
    match = find_registry_for_pool(Path(pool_path), cfg)
    if match is None:
        return None
    _, entry = match
    return load_hydration_config(Path(entry.pool_home))


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
    content_type: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    timestamp: str | None = None,
    meta: dict[str, object] | None = None,
    related_to: list[str] | None = None,
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
        content_type: Optional content format. One of: text, gdoc, slack, jira,
            confluence, email, image, figma, task. Defaults to omitted (implicitly text).
        status: 'active' (default/omit), 'archived' (historical, excluded
            from default queries), or 'suppressed' (relevant but verbose,
            excluded from default queries).
        tags: Optional list of semantic labels (e.g. ['repair', 'crv']).
        timestamp: ISO-8601 timestamp. Defaults to now (UTC). Set explicitly
            for backdated or imported nodes.
        meta: Optional key-value metadata (e.g. {'priority': 'high', 'url': '...'}).
        related_to: Optional list of related node IDs for cross-references.

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
        source=_MCP_SOURCE,
        node_type=node_type,
        context=context,
        creator=creator,
        content=content,
        content_type=content_type,
        status=status,
        tags=tags or [],
        timestamp=timestamp,
        meta=meta,
        related_to=related_to,
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
    config_dir: str | None = None,
) -> dict[str, Any]:
    """Show the full content of a node by ID.

    Use this after tool_list_nodes to read the body text and all metadata
    fields of a specific node. When the node's content_type matches a type
    declared in the registry's hydration.yaml, hydration_instructions will
    contain resolution guidance for the LLM.

    Args:
        pool_path: Absolute path to the pool directory.
        node_id: 12-character node ID (from tool_list_nodes output).
        config_dir: Optional config directory override (for testing).

    Returns:
        dict with keys:
            found: True if the node exists, False otherwise
            node: full node detail (when found=True), with keys:
                node_id, context, node_type, status, timestamp, source,
                creator, body, tags, related_to, meta, hydration_instructions
    """
    if node_id in ("latest", "_latest"):
        node_id = LATEST_NODE_ID
    hydration = _load_hydration_for_pool(pool_path, config_dir)
    with _resolve_pool(pool_path) as pool:
        detail = show_node(pool, node_id, hydration=hydration)
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
            "hydration_instructions": detail.hydration_instructions,
        },
    }


def tool_validate_pool(
    *,
    pool_path: str,
    config_dir: str | None = None,
) -> dict[str, Any]:
    """Validate all nodes in a pool against the v1 schema.

    Use after bulk imports or before committing a pool to confirm all nodes
    are schema-compliant. Reports each invalid node and its specific errors.
    Content types declared in the registry's hydration.yaml are accepted
    without meta validation.

    Args:
        pool_path: Absolute path to the pool directory.
        config_dir: Optional config directory override (for testing).

    Returns:
        dict with keys:
            valid: True if all nodes pass validation
            error_count: total number of validation errors found
            errors: list of dicts, each with 'file' and 'errors' keys
    """
    hydration = _load_hydration_for_pool(pool_path, config_dir)
    registry_types = hydration.declared_types if hydration and hydration.declared_types else None
    all_errors: list[dict[str, Any]] = []

    with _resolve_pool(pool_path) as pool:
        # Validate latest node if present.
        latest_file = pool / LATEST_FILENAME
        if latest_file.exists():
            lfm = extract_frontmatter(latest_file.read_text())
            if lfm is None:
                all_errors.append({
                    "file": latest_file.name,
                    "errors": ["no frontmatter found"],
                })
            else:
                lvr = validate_node(lfm, registry_types=registry_types)
                if not lvr.valid:
                    all_errors.append({
                        "file": latest_file.name,
                        "errors": lvr.errors,
                    })

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
                result = validate_node(frontmatter, registry_types=registry_types)
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


def tool_update_node(
    *,
    pool_path: str,
    node_id: str,
    status: str | None = None,
    tags_add: list[str] | None = None,
    tags_remove: list[str] | None = None,
    meta: dict[str, object] | None = None,
    content: str | None = None,
    context: str | None = None,
    content_type: str | None = None,
    node_type: str | None = None,
    related_add: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing node's frontmatter and/or body.

    When node_type changes between 'live' and 'snapshot', the file is
    moved between live/ and snapshots/ directories. Use this to freeze
    a live node: set node_type='snapshot' and content=<hydrated body>.

    Args:
        pool_path: Absolute path to the pool directory.
        node_id: 12-character node ID to update.
        status: New status (active, archived, suppressed).
        tags_add: Tags to merge into existing list.
        tags_remove: Tags to remove from existing list.
        meta: Key-value pairs to merge into existing meta.
        content: New body text (replaces entire body).
        context: New context description.
        content_type: New content type.
        node_type: New node type (snapshot, snap, live). Moves file if changed.
        related_add: Related node IDs to append.

    Returns:
        dict with status 'updated', 'noop', or 'error'.
    """
    if is_remote_registry(pool_path):
        return {
            "status": "error",
            "errors": [
                "Write operations on remote pools are not supported in RO mode. "
                "Use a local pool path or set mode: rw in config."
            ],
        }
    result = update_node(
        pool_path=Path(pool_path),
        node_id=node_id,
        status=status,
        tags_add=tags_add,
        tags_remove=tags_remove,
        meta=meta,
        content=content,
        context=context,
        content_type=content_type,
        node_type=node_type,
        related_add=related_add,
    )
    if not result.valid:
        return {"status": "error", "errors": result.errors}
    if result.noop:
        return {"status": "noop", "node_id": node_id}
    return {
        "status": "updated",
        "node_id": result.node_id,
        "path": str(result.path),
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
    content_type: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    timestamp: str | None = None,
    meta: dict[str, object] | None = None,
    related_to: list[str] | None = None,
) -> dict[str, Any]:
    """Create a context node in a pool. See tool_add_node for full docs."""
    return tool_add_node(
        pool_path=pool_path,
        context=context,
        creator=creator,
        node_type=node_type,
        content=content,
        content_type=content_type,
        status=status,
        tags=tags,
        timestamp=timestamp,
        meta=meta,
        related_to=related_to,
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


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update context node",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def update_pool_node(
    pool_path: str,
    node_id: str,
    status: str | None = None,
    tags_add: list[str] | None = None,
    tags_remove: list[str] | None = None,
    meta: dict[str, object] | None = None,
    content: str | None = None,
    context: str | None = None,
    content_type: str | None = None,
    node_type: str | None = None,
    related_add: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing node. See tool_update_node for full docs."""
    return tool_update_node(
        pool_path=pool_path,
        node_id=node_id,
        status=status,
        tags_add=tags_add,
        tags_remove=tags_remove,
        meta=meta,
        content=content,
        context=context,
        content_type=content_type,
        node_type=node_type,
        related_add=related_add,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search pool nodes",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def search_pool_nodes(
    pool_path: str,
    query: str,
) -> dict[str, Any]:
    """Search node frontmatter and body text for a keyword.

    Searches context, tags, meta values, and body text across all nodes
    in the pool (both snapshots/ and live/). Case-insensitive.

    Args:
        pool_path: Absolute path to the pool directory.
        query: Search string to match against node content.

    Returns:
        dict with keys:
            count: number of matching nodes
            results: list of matches, each with:
                node_id, context, content_type, source, matches (excerpts)
    """
    with _resolve_pool(pool_path) as pool:
        results = search_nodes(pool_path=pool, query=query)
    return {
        "count": len(results),
        "results": [
            {
                "node_id": r.node_id,
                "context": r.context,
                "content_type": r.content_type,
                "source": r.source,
                "matches": r.matches[:5],
            }
            for r in results
        ],
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search barrel cache",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def search_pool_barrel(
    pool_path: str,
    query: str,
) -> dict[str, Any]:
    """Search barrel cached content for a keyword (deep search).

    Searches the hydrated content cached in the pool's barrel/ directory.
    Only finds content that has been previously hydrated and cached.
    Case-insensitive.

    Args:
        pool_path: Absolute path to the pool directory.
        query: Search string to match against cached content.

    Returns:
        dict with keys:
            count: number of matching barrel entries
            results: list of matches, each with:
                node_id, content_type, source, matches (excerpts)
    """
    with _resolve_pool(pool_path) as pool:
        results = search_barrel(pool_path=pool, query=query)
    return {
        "count": len(results),
        "results": [
            {
                "node_id": r.node_id,
                "content_type": r.content_type,
                "source": r.source,
                "matches": r.matches[:5],
            }
            for r in results
        ],
    }


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
