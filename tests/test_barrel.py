"""Tests for barrel — hydration cache for live node content."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from alph.core import (
    BarrelEntry,
    BarrelMeta,
    BarrelStatus,
    HydrationConfig,
    HydrationTypeConfig,
    barrel_check,
    barrel_export,
    barrel_flush,
    barrel_invalidate,
    barrel_mark_read,
    barrel_new,
    barrel_status,
    barrel_write,
    load_barrel_config,
    load_hydration_config,
)

BARREL_DIR = "barrel"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pool(tmp_path: Path) -> Path:
    """Create a minimal pool with live/ and snapshots/ dirs."""
    pool_path = tmp_path / "mypool"
    (pool_path / "live").mkdir(parents=True)
    (pool_path / "snapshots").mkdir(parents=True)
    return pool_path


@pytest.fixture()
def registry(tmp_path: Path) -> Path:
    """Create a minimal registry root with hydration.yaml."""
    reg = tmp_path / "registry"
    reg.mkdir()
    hydration = {
        "types": {
            "gdoc": {"provider": "mdsync", "instructions": "fetch gdoc"},
            "jira": {"provider": "atlassian-mcp", "instructions": "fetch jira"},
            "slack": {"provider": "slack-mcp", "instructions": "fetch slack"},
        },
        "barrel": {
            "default_ttl": "4h",
            "types": {
                "snapshot": {"ttl": "forever", "fetch_mode": "full"},
                "gdoc": {"ttl": "4h", "fetch_mode": "full"},
                "jira": {"ttl": "2h", "fetch_mode": "full"},
                "slack": {"ttl": "1h", "fetch_mode": "delta"},
            },
        },
    }
    (reg / "hydration.yaml").write_text(yaml.dump(hydration))
    return reg


# ---------------------------------------------------------------------------
# BarrelEntry dataclass
# ---------------------------------------------------------------------------


class TestBarrelEntry:
    def test_barrel_entry_is_frozen(self) -> None:
        entry = BarrelEntry(
            node_id="abc123",
            content_type="gdoc",
            cached_at="2026-03-16T14:30:00+00:00",
            content="# Hello",
        )
        with pytest.raises(AttributeError):
            entry.node_id = "xyz"  # type: ignore[misc]

    def test_barrel_entry_defaults(self) -> None:
        entry = BarrelEntry(
            node_id="abc123",
            content_type="gdoc",
            cached_at="2026-03-16T14:30:00+00:00",
            content="# Hello",
        )
        assert entry.cached_through == ""
        assert entry.fetch_mode == "full"


# ---------------------------------------------------------------------------
# BarrelMeta dataclass
# ---------------------------------------------------------------------------


class TestBarrelMeta:
    def test_barrel_meta_is_frozen(self) -> None:
        meta = BarrelMeta()
        with pytest.raises(AttributeError):
            meta.last_read = "x"  # type: ignore[misc]

    def test_barrel_meta_defaults(self) -> None:
        meta = BarrelMeta()
        assert meta.last_read is None


# ---------------------------------------------------------------------------
# load_barrel_config
# ---------------------------------------------------------------------------


class TestLoadBarrelConfig:
    def test_loads_barrel_section_from_hydration_yaml(self, registry: Path) -> None:
        hydration = load_hydration_config(registry)
        config = load_barrel_config(registry)
        assert config.default_ttl == "4h"
        assert "gdoc" in config.types
        assert config.types["gdoc"].ttl == "4h"
        assert config.types["slack"].fetch_mode == "delta"

    def test_missing_barrel_section_returns_defaults(self, tmp_path: Path) -> None:
        reg = tmp_path / "reg"
        reg.mkdir()
        (reg / "hydration.yaml").write_text(yaml.dump({"types": {"gdoc": {"provider": "x"}}}))
        config = load_barrel_config(reg)
        assert config.default_ttl == "4h"
        assert config.types == {}

    def test_missing_hydration_yaml_returns_defaults(self, tmp_path: Path) -> None:
        reg = tmp_path / "reg"
        reg.mkdir()
        config = load_barrel_config(reg)
        assert config.default_ttl == "4h"


# ---------------------------------------------------------------------------
# barrel_write
# ---------------------------------------------------------------------------


class TestBarrelWrite:
    def test_writes_barrel_file_with_frontmatter(self, pool: Path) -> None:
        result = barrel_write(
            pool_path=pool,
            node_id="abc123def456",
            content_type="gdoc",
            content="# Design Doc\n\nContent here.",
        )
        assert result.node_id == "abc123def456"
        barrel_file = pool / BARREL_DIR / "abc123def456.md"
        assert barrel_file.exists()
        text = barrel_file.read_text()
        assert "node_id: abc123def456" in text
        assert "content_type: gdoc" in text
        assert "cached_at:" in text
        assert "# Design Doc" in text

    def test_creates_barrel_dir_if_missing(self, pool: Path) -> None:
        assert not (pool / BARREL_DIR).exists()
        barrel_write(pool_path=pool, node_id="abc123", content_type="gdoc", content="x")
        assert (pool / BARREL_DIR).exists()

    def test_overwrites_existing_cache(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="abc123", content_type="gdoc", content="old")
        barrel_write(pool_path=pool, node_id="abc123", content_type="gdoc", content="new")
        barrel_file = pool / BARREL_DIR / "abc123.md"
        assert "new" in barrel_file.read_text()
        assert "old" not in barrel_file.read_text()

    def test_write_with_cached_through(self, pool: Path) -> None:
        barrel_write(
            pool_path=pool,
            node_id="abc123",
            content_type="slack",
            content="messages",
            cached_through="2026-03-16T15:00:00+00:00",
            fetch_mode="delta",
        )
        text = (pool / BARREL_DIR / "abc123.md").read_text()
        assert "cached_through: '2026-03-16T15:00:00+00:00'" in text or "cached_through:" in text
        assert "fetch_mode: delta" in text


# ---------------------------------------------------------------------------
# barrel_check
# ---------------------------------------------------------------------------


class TestBarrelCheck:
    def test_missing_returns_missing(self, pool: Path) -> None:
        result = barrel_check(pool_path=pool, node_id="nonexistent", default_ttl="4h")
        assert result == "missing"

    def test_fresh_entry_returns_fresh(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="abc123", content_type="gdoc", content="x")
        result = barrel_check(pool_path=pool, node_id="abc123", default_ttl="4h")
        assert result == "fresh"

    def test_stale_entry_returns_stale(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="abc123", content_type="gdoc", content="x")
        # Manually backdate the cached_at
        barrel_file = pool / BARREL_DIR / "abc123.md"
        text = barrel_file.read_text()
        old_time = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        # Replace the cached_at line
        lines = text.split("\n")
        new_lines = []
        for line in lines:
            if line.startswith("cached_at:"):
                new_lines.append(f"cached_at: '{old_time}'")
            else:
                new_lines.append(line)
        barrel_file.write_text("\n".join(new_lines))
        result = barrel_check(pool_path=pool, node_id="abc123", default_ttl="4h")
        assert result == "stale"

    def test_forever_ttl_always_fresh(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="abc123", content_type="gdoc", content="x")
        # Backdate
        barrel_file = pool / BARREL_DIR / "abc123.md"
        text = barrel_file.read_text()
        old_time = (datetime.now(UTC) - timedelta(days=365)).isoformat()
        lines = text.split("\n")
        new_lines = []
        for line in lines:
            if line.startswith("cached_at:"):
                new_lines.append(f"cached_at: '{old_time}'")
            else:
                new_lines.append(line)
        barrel_file.write_text("\n".join(new_lines))
        result = barrel_check(pool_path=pool, node_id="abc123", default_ttl="forever")
        assert result == "fresh"

    def test_type_specific_ttl(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="abc123", content_type="gdoc", content="x")
        # Backdate by 3 hours (stale for 2h jira TTL, fresh for 4h gdoc TTL)
        barrel_file = pool / BARREL_DIR / "abc123.md"
        text = barrel_file.read_text()
        old_time = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        lines = text.split("\n")
        new_lines = []
        for line in lines:
            if line.startswith("cached_at:"):
                new_lines.append(f"cached_at: '{old_time}'")
            else:
                new_lines.append(line)
        barrel_file.write_text("\n".join(new_lines))
        # With 4h TTL, should be fresh
        assert barrel_check(pool_path=pool, node_id="abc123", default_ttl="4h") == "fresh"
        # With 2h TTL, should be stale
        assert barrel_check(pool_path=pool, node_id="abc123", default_ttl="2h") == "stale"


# ---------------------------------------------------------------------------
# barrel_status
# ---------------------------------------------------------------------------


class TestBarrelStatus:
    def test_empty_pool_returns_empty_list(self, pool: Path) -> None:
        entries = barrel_status(pool_path=pool, default_ttl="4h")
        assert entries == []

    def test_returns_all_cached_entries(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="doc1")
        barrel_write(pool_path=pool, node_id="bbb222", content_type="jira", content="issue1")
        entries = barrel_status(pool_path=pool, default_ttl="4h")
        assert len(entries) == 2
        ids = {e.node_id for e in entries}
        assert ids == {"aaa111", "bbb222"}

    def test_status_includes_freshness(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="x")
        entries = barrel_status(pool_path=pool, default_ttl="4h")
        assert len(entries) == 1
        assert entries[0].freshness == "fresh"


# ---------------------------------------------------------------------------
# barrel_invalidate
# ---------------------------------------------------------------------------


class TestBarrelInvalidate:
    def test_invalidate_removes_specific_entry(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="abc123", content_type="gdoc", content="x")
        assert (pool / BARREL_DIR / "abc123.md").exists()
        removed = barrel_invalidate(pool_path=pool, node_id="abc123")
        assert removed
        assert not (pool / BARREL_DIR / "abc123.md").exists()

    def test_invalidate_nonexistent_returns_false(self, pool: Path) -> None:
        removed = barrel_invalidate(pool_path=pool, node_id="nonexistent")
        assert not removed


# ---------------------------------------------------------------------------
# barrel_flush
# ---------------------------------------------------------------------------


class TestBarrelFlush:
    def test_flush_removes_all_entries(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="x")
        barrel_write(pool_path=pool, node_id="bbb222", content_type="jira", content="y")
        count = barrel_flush(pool_path=pool)
        assert count == 2
        assert not list((pool / BARREL_DIR).glob("*.md"))

    def test_flush_preserves_meta_file(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="x")
        barrel_mark_read(pool_path=pool)
        barrel_flush(pool_path=pool)
        # Meta file should still exist
        assert (pool / BARREL_DIR / ".barrel-meta.yaml").exists()

    def test_flush_empty_barrel_returns_zero(self, pool: Path) -> None:
        count = barrel_flush(pool_path=pool)
        assert count == 0


# ---------------------------------------------------------------------------
# barrel_new / barrel_mark_read (timeline)
# ---------------------------------------------------------------------------


class TestBarrelTimeline:
    def test_mark_read_creates_meta_file(self, pool: Path) -> None:
        barrel_mark_read(pool_path=pool)
        meta_file = pool / BARREL_DIR / ".barrel-meta.yaml"
        assert meta_file.exists()
        data = yaml.safe_load(meta_file.read_text())
        assert "last_read" in data

    def test_new_returns_entries_newer_than_last_read(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="x")
        barrel_mark_read(pool_path=pool)
        barrel_write(pool_path=pool, node_id="bbb222", content_type="jira", content="y")
        new_entries = barrel_new(pool_path=pool)
        assert len(new_entries) == 1
        assert new_entries[0].node_id == "bbb222"

    def test_new_returns_all_when_never_read(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="x")
        barrel_write(pool_path=pool, node_id="bbb222", content_type="jira", content="y")
        new_entries = barrel_new(pool_path=pool)
        assert len(new_entries) == 2

    def test_new_returns_empty_when_all_read(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="x")
        barrel_mark_read(pool_path=pool)
        new_entries = barrel_new(pool_path=pool)
        assert new_entries == []


# ---------------------------------------------------------------------------
# barrel_export
# ---------------------------------------------------------------------------


class TestBarrelExport:
    def test_export_markdown(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="# Doc One")
        barrel_write(pool_path=pool, node_id="bbb222", content_type="jira", content="Issue content")
        output = barrel_export(pool_path=pool, fmt="md")
        assert "# Doc One" in output
        assert "Issue content" in output
        assert "aaa111" in output
        assert "bbb222" in output

    def test_export_json(self, pool: Path) -> None:
        barrel_write(pool_path=pool, node_id="aaa111", content_type="gdoc", content="# Doc")
        output = barrel_export(pool_path=pool, fmt="json")
        import json
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["node_id"] == "aaa111"

    def test_export_empty_pool(self, pool: Path) -> None:
        output = barrel_export(pool_path=pool, fmt="md")
        assert output == ""
