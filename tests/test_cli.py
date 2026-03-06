"""Behavior tests for the alph CLI."""

import yaml
from pathlib import Path

from typer.testing import CliRunner

from alph.cli import app
from alph.core import extract_frontmatter

runner = CliRunner()


def _init_registry_and_pool(base: Path) -> Path:
    """Create a minimal registry + pool using the CLI, return pool path."""
    runner.invoke(app, ["registry", "init", "--path", str(base / "registry"),
                        "--id", "reg-01", "--context", "Test registry"])
    runner.invoke(app, ["pool", "init", "--registry", str(base / "registry"),
                        "--name", "test-pool", "--context", "Test pool"])
    return base / "registry" / "test-pool"


def test_registry_init_creates_config(tmp_path: Path) -> None:
    """alph registry init creates a config.yaml in the specified path."""
    result = runner.invoke(app, [
        "registry", "init",
        "--path", str(tmp_path / "registry"),
        "--id", "reg-01",
        "--context", "Personal context pools",
    ])
    assert result.exit_code == 0
    assert (tmp_path / "registry" / "config.yaml").exists()


def test_pool_init_creates_pool_structure(tmp_path: Path) -> None:
    """alph pool init creates snapshots/, pointers/, and .alph/ directories."""
    runner.invoke(app, ["registry", "init", "--path", str(tmp_path / "reg"),
                        "--id", "r1", "--context", "Test"])
    result = runner.invoke(app, [
        "pool", "init",
        "--registry", str(tmp_path / "reg"),
        "--name", "vehicles",
        "--context", "Vehicle maintenance",
    ])
    assert result.exit_code == 0
    pool = tmp_path / "reg" / "vehicles"
    assert (pool / "snapshots").is_dir()
    assert (pool / "pointers").is_dir()


def test_add_creates_node_file(tmp_path: Path) -> None:
    """alph add -c <text> creates a node file in the pool's snapshots/."""
    pool = _init_registry_and_pool(tmp_path)
    result = runner.invoke(app, [
        "add", "-c", "Oil change at Valvoline",
        "--pool", str(pool),
        "--creator", "chase@example.com",
    ])
    assert result.exit_code == 0
    nodes = list((pool / "snapshots").glob("*.md"))
    assert len(nodes) == 1


def test_add_node_frontmatter_is_valid(tmp_path: Path) -> None:
    """Node created via alph add has valid schema-compliant frontmatter."""
    from alph.core import validate_node
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Brake pads at 40%",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    node_file = next((pool / "snapshots").glob("*.md"))
    frontmatter = extract_frontmatter(node_file.read_text())
    assert frontmatter is not None
    assert validate_node(frontmatter).valid is True


def test_list_shows_node_context(tmp_path: Path) -> None:
    """alph list outputs the context of each node in the pool."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Oil change at Valvoline",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool)])
    assert result.exit_code == 0
    assert "Oil change at Valvoline" in result.output


def test_show_displays_full_node(tmp_path: Path) -> None:
    """alph show <id> outputs the full node content."""
    pool = _init_registry_and_pool(tmp_path)
    add_result = runner.invoke(app, ["add", "-c", "Tire rotation",
                                     "--pool", str(pool), "--creator", "chase@example.com"])
    # ID is echoed in the add output
    node_id = next((pool / "snapshots").glob("*.md")).stem
    result = runner.invoke(app, ["show", node_id, "--pool", str(pool)])
    assert result.exit_code == 0
    assert "Tire rotation" in result.output


def test_validate_passes_for_valid_pool(tmp_path: Path) -> None:
    """alph validate exits 0 for a pool with valid nodes."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Oil change",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    result = runner.invoke(app, ["validate", "--pool", str(pool)])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Status flag on list and add
# ---------------------------------------------------------------------------


def test_list_excludes_archived_by_default(tmp_path: Path) -> None:
    """alph list omits archived nodes without -s flag."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Active node",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    runner.invoke(app, ["add", "-c", "Archived node", "--status", "archived",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool)])
    assert result.exit_code == 0
    assert "Active node" in result.output
    assert "Archived node" not in result.output


def test_list_includes_archived_with_status_flag(tmp_path: Path) -> None:
    """alph list -s archived includes active and archived nodes."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Active node",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    runner.invoke(app, ["add", "-c", "Archived node", "--status", "archived",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool), "-s", "archived"])
    assert result.exit_code == 0
    assert "Active node" in result.output
    assert "Archived node" in result.output


def test_list_includes_all_nodes_with_status_all(tmp_path: Path) -> None:
    """alph list -s all includes active, archived, and suppressed nodes."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Active node",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    runner.invoke(app, ["add", "-c", "Archived node", "--status", "archived",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    runner.invoke(app, ["add", "-c", "Suppressed node", "--status", "suppressed",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool), "-s", "all"])
    assert result.exit_code == 0
    assert "Active node" in result.output
    assert "Archived node" in result.output
    assert "Suppressed node" in result.output


def test_add_with_status_writes_status_to_frontmatter(tmp_path: Path) -> None:
    """alph add --status archived writes status field to node frontmatter."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Old note", "--status", "archived",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    node_file = next((pool / "snapshots").glob("*.md"))
    frontmatter = extract_frontmatter(node_file.read_text())
    assert frontmatter is not None
    assert frontmatter["status"] == "archived"


# ---------------------------------------------------------------------------
# Config defaults wired into CLI
# ---------------------------------------------------------------------------


def _write_global_config(global_dir: Path, content: dict) -> None:
    """Write a global alph config file."""
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(yaml.dump(content))


def test_add_uses_config_creator_when_creator_flag_omitted(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph add omits --creator when creator is set in global config."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {"creator": "config@example.com"})
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    pool = _init_registry_and_pool(tmp_path)
    result = runner.invoke(app, ["add", "-c", "Node from config creator",
                                 "--pool", str(pool)])
    assert result.exit_code == 0
    node_file = next((pool / "snapshots").glob("*.md"))
    fm = extract_frontmatter(node_file.read_text())
    assert fm is not None
    assert fm["creator"] == "config@example.com"


def test_list_uses_config_default_pool_when_pool_flag_omitted(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph list resolves pool from config when --pool is not given."""
    pool = _init_registry_and_pool(tmp_path)
    registry = tmp_path / "registry"
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "creator": "config@example.com",
        "default_registry": "reg-01",
        "default_pool": "test-pool",
        "registries": {"reg-01": str(registry)},
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    runner.invoke(app, ["add", "-c", "Config-default-pool node",
                        "--pool", str(pool), "--creator", "config@example.com"])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "Config-default-pool node" in result.output


def test_add_errors_when_no_creator_and_no_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph add exits non-zero when --creator is omitted and no config creator is set."""
    global_dir = tmp_path / "empty-global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    pool = _init_registry_and_pool(tmp_path)
    result = runner.invoke(app, ["add", "-c", "No creator node", "--pool", str(pool)])
    assert result.exit_code != 0
