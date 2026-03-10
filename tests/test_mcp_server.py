"""Behavior tests for the alph MCP server tools."""

from pathlib import Path

from alph.core import create_node, init_pool, init_registry
from alph.mcp_server import (
    tool_add_node,
    tool_list_nodes,
    tool_show_node,
    tool_validate_pool,
)


def _setup_pool(base: Path) -> Path:
    """Create a registry + pool and return the pool path."""
    registry_path = base / "registry"
    global_dir = base / "global"
    init_registry(pool_home=registry_path, registry_id="test", context="Test registry",
                  global_config_dir=global_dir)
    result = init_pool(
        registry_id="test",
        name="vehicles",
        context="Vehicle maintenance",
        cwd=registry_path,
        global_config_dir=global_dir,
    )
    return result.pool_path


# ---------------------------------------------------------------------------
# tool_add_node
# ---------------------------------------------------------------------------


def test_tool_add_node_creates_node_and_returns_id(tmp_path: Path) -> None:
    """tool_add_node creates a node and returns its ID in the response."""
    pool = _setup_pool(tmp_path)
    response = tool_add_node(
        pool_path=str(pool),
        context="Oil change at Valvoline",
        creator="chase@example.com",
    )
    assert response["status"] == "created"
    assert len(response["node_id"]) == 12
    assert response["node_type"] == "snapshot"


def test_tool_add_node_reports_duplicate(tmp_path: Path) -> None:
    """tool_add_node returns duplicate status when node already exists."""
    pool = _setup_pool(tmp_path)
    kwargs = {
        "pool_path": str(pool),
        "context": "Oil change at Valvoline",
        "creator": "chase@example.com",
        "timestamp": "2026-03-05T10:00:00Z",
    }
    tool_add_node(**kwargs)
    response = tool_add_node(**kwargs)
    assert response["status"] == "duplicate"
    assert response["existing_creator"] == "chase@example.com"


def test_tool_add_node_accepts_live_type(tmp_path: Path) -> None:
    """tool_add_node creates a live node when node_type is live."""
    pool = _setup_pool(tmp_path)
    response = tool_add_node(
        pool_path=str(pool),
        context="Jira ticket MAINT-42",
        creator="chase@example.com",
        node_type="live",
    )
    assert response["node_type"] == "live"


def test_tool_add_node_accepts_status(tmp_path: Path) -> None:
    """tool_add_node writes the status field when provided."""
    pool = _setup_pool(tmp_path)
    response = tool_add_node(
        pool_path=str(pool),
        context="Archived old note",
        creator="chase@example.com",
        status="archived",
    )
    assert response["status"] == "created"
    assert response.get("node_status") == "archived"


# ---------------------------------------------------------------------------
# tool_list_nodes
# ---------------------------------------------------------------------------


def test_tool_list_nodes_returns_active_nodes(tmp_path: Path) -> None:
    """tool_list_nodes returns active nodes as a list in the response."""
    pool = _setup_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Oil change", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    response = tool_list_nodes(pool_path=str(pool))
    assert response["count"] == 1
    assert response["nodes"][0]["context"] == "Oil change"


def test_tool_list_nodes_excludes_archived_by_default(tmp_path: Path) -> None:
    """tool_list_nodes omits archived nodes unless include_statuses is passed."""
    pool = _setup_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Active note", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Archived note", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="archived")
    response = tool_list_nodes(pool_path=str(pool))
    assert response["count"] == 1
    assert response["nodes"][0]["context"] == "Active note"


def test_tool_list_nodes_includes_archived_when_requested(tmp_path: Path) -> None:
    """tool_list_nodes includes archived nodes when include_statuses contains archived."""
    pool = _setup_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Active note", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Archived note", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="archived")
    response = tool_list_nodes(pool_path=str(pool), include_statuses=["archived"])
    assert response["count"] == 2


def test_tool_list_nodes_empty_pool(tmp_path: Path) -> None:
    """tool_list_nodes returns count 0 for an empty pool."""
    pool = _setup_pool(tmp_path)
    response = tool_list_nodes(pool_path=str(pool))
    assert response["count"] == 0
    assert response["nodes"] == []


# ---------------------------------------------------------------------------
# tool_show_node
# ---------------------------------------------------------------------------


def test_tool_show_node_returns_full_node(tmp_path: Path) -> None:
    """tool_show_node returns complete node details for a known ID."""
    pool = _setup_pool(tmp_path)
    result = create_node(
        pool_path=pool, source="cli", node_type="snapshot",
        context="Brake pads at 40%", creator="chase@example.com",
        timestamp="2026-03-05T10:00:00Z", content="Check again at 110k miles.",
    )
    response = tool_show_node(pool_path=str(pool), node_id=result.node_id)
    assert response["found"] is True
    assert response["node"]["context"] == "Brake pads at 40%"
    assert "110k miles" in response["node"]["body"]


def test_tool_show_node_returns_not_found_for_unknown_id(tmp_path: Path) -> None:
    """tool_show_node returns found=False for an ID that does not exist."""
    pool = _setup_pool(tmp_path)
    response = tool_show_node(pool_path=str(pool), node_id="nonexistent1")
    assert response["found"] is False


# ---------------------------------------------------------------------------
# tool_validate_pool
# ---------------------------------------------------------------------------


def test_tool_validate_pool_passes_for_valid_nodes(tmp_path: Path) -> None:
    """tool_validate_pool returns valid=True for a pool with schema-compliant nodes."""
    pool = _setup_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Valid node", creator="chase@example.com")
    response = tool_validate_pool(pool_path=str(pool))
    assert response["valid"] is True
    assert response["error_count"] == 0


def test_tool_validate_pool_reports_errors_for_invalid_node(tmp_path: Path) -> None:
    """tool_validate_pool returns valid=False and lists errors for bad frontmatter."""
    pool = _setup_pool(tmp_path)
    bad_node = pool / "snapshots" / "bad.md"
    bad_node.write_text("---\nschema_version: '1'\n---\n")  # missing required fields
    response = tool_validate_pool(pool_path=str(pool))
    assert response["valid"] is False
    assert response["error_count"] > 0
