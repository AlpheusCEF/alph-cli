"""Tests for alph search and barrel search."""

from pathlib import Path

import pytest
import yaml

from alph.core import (
    SearchResult,
    barrel_write,
    search_barrel,
    search_nodes,
)


@pytest.fixture()
def pool(tmp_path: Path) -> Path:
    """Create a pool with some nodes."""
    pool_path = tmp_path / "mypool"
    (pool_path / "live").mkdir(parents=True)
    (pool_path / "snapshots").mkdir(parents=True)

    # Snapshot node with body
    (pool_path / "snapshots" / "aaa111bbb222.md").write_text(
        "---\n"
        "id: aaa111bbb222\n"
        "node_type: snapshot\n"
        "context: OAuth token rotation decision\n"
        "creator: chase\n"
        "content_type: text\n"
        "tags: [auth, security]\n"
        "schema_version: '1'\n"
        "timestamp: '2026-03-16T00:00:00Z'\n"
        "source: cli\n"
        "---\n"
        "We decided to use PKCE flow instead of implicit grant.\n"
        "This improves security for mobile clients.\n"
    )

    # Live node (no body)
    (pool_path / "live" / "ccc333ddd444.md").write_text(
        "---\n"
        "id: ccc333ddd444\n"
        "node_type: live\n"
        "context: Social Auth Platform design doc covering architecture\n"
        "creator: chase\n"
        "content_type: gdoc\n"
        "tags: [design, architecture]\n"
        "meta:\n"
        "  url: https://docs.google.com/document/d/abc123\n"
        "schema_version: '1'\n"
        "timestamp: '2026-03-16T00:00:00Z'\n"
        "source: cli\n"
        "---\n"
    )

    # Another snapshot
    (pool_path / "snapshots" / "eee555fff666.md").write_text(
        "---\n"
        "id: eee555fff666\n"
        "node_type: snapshot\n"
        "context: Credential Manager migration plan\n"
        "creator: chase\n"
        "content_type: text\n"
        "tags: [migration]\n"
        "schema_version: '1'\n"
        "timestamp: '2026-03-16T00:00:00Z'\n"
        "source: cli\n"
        "---\n"
        "Migration from legacy OAuth to Credential Manager API.\n"
        "Target: Android 14+ devices.\n"
    )

    return pool_path


# ---------------------------------------------------------------------------
# search_nodes
# ---------------------------------------------------------------------------


class TestSearchNodes:
    def test_search_matches_context_field(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="OAuth")
        assert len(results) >= 1
        ids = {r.node_id for r in results}
        assert "aaa111bbb222" in ids

    def test_search_matches_body_text(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="PKCE")
        assert len(results) == 1
        assert results[0].node_id == "aaa111bbb222"

    def test_search_matches_tags(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="architecture")
        ids = {r.node_id for r in results}
        assert "ccc333ddd444" in ids

    def test_search_matches_meta_values(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="abc123")
        assert len(results) == 1
        assert results[0].node_id == "ccc333ddd444"

    def test_search_case_insensitive(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="oauth")
        assert len(results) >= 1

    def test_search_no_matches_returns_empty(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="nonexistent_xyz")
        assert results == []

    def test_search_returns_context_and_content_type(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="PKCE")
        assert results[0].context == "OAuth token rotation decision"
        assert results[0].content_type == "text"
        assert results[0].source == "node"

    def test_search_returns_matching_excerpts(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="PKCE")
        assert len(results[0].matches) >= 1
        assert any("PKCE" in m for m in results[0].matches)

    def test_search_multiple_nodes(self, pool: Path) -> None:
        results = search_nodes(pool_path=pool, query="migration")
        ids = {r.node_id for r in results}
        # "migration" appears in eee555fff666 context and body
        assert "eee555fff666" in ids

    def test_search_empty_pool(self, tmp_path: Path) -> None:
        pool = tmp_path / "empty"
        (pool / "live").mkdir(parents=True)
        (pool / "snapshots").mkdir(parents=True)
        results = search_nodes(pool_path=pool, query="anything")
        assert results == []


# ---------------------------------------------------------------------------
# search_barrel
# ---------------------------------------------------------------------------


class TestSearchBarrel:
    def test_search_barrel_matches_cached_content(self, pool: Path) -> None:
        barrel_write(
            pool_path=pool,
            node_id="aaa111bbb222",
            content_type="gdoc",
            content="# Design Doc\n\nWe use PKCE flow for all mobile OAuth.\nThis replaces implicit grant.",
        )
        results = search_barrel(pool_path=pool, query="PKCE")
        assert len(results) == 1
        assert results[0].node_id == "aaa111bbb222"
        assert results[0].source == "barrel"

    def test_search_barrel_case_insensitive(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="x1", content_type="gdoc", content="OAuth flow")
        results = search_barrel(pool_path=pool, query="oauth")
        assert len(results) == 1

    def test_search_barrel_no_matches(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="x1", content_type="gdoc", content="Hello world")
        results = search_barrel(pool_path=pool, query="nonexistent")
        assert results == []

    def test_search_barrel_empty(self, pool: Path) -> None:
        results = search_barrel(pool_path=pool, query="anything")
        assert results == []

    def test_search_barrel_returns_excerpts(self, pool: Path) -> None:
        barrel_write(
            pool_path=pool,
            node_id="x1",
            content_type="confluence",
            content="Line one.\nLine with keyword here.\nLine three.",
        )
        results = search_barrel(pool_path=pool, query="keyword")
        assert len(results) == 1
        assert any("keyword" in m for m in results[0].matches)

    def test_search_barrel_multiple_entries(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="x1", content_type="gdoc", content="OAuth PKCE flow")
        barrel_write(pool_path=pool, node_id="x2", content_type="jira", content="Implement PKCE")
        results = search_barrel(pool_path=pool, query="PKCE")
        assert len(results) == 2


# ---------------------------------------------------------------------------
# SearchResult dataclass
# ---------------------------------------------------------------------------


class TestSearchResult:
    def test_search_result_is_frozen(self) -> None:
        r = SearchResult(node_id="x", context="c", content_type="text", matches=[], source="node")
        with pytest.raises(AttributeError):
            r.node_id = "y"  # type: ignore[misc]
