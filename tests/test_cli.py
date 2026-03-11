"""Behavior tests for the alph CLI."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from typer.testing import CliRunner

from alph.cli import app
from alph.core import extract_frontmatter
from alph.remote import FileEntry

runner = CliRunner()


def _init_registry_and_pool(base: Path) -> Path:
    """Create a minimal registry + pool using the CLI, return pool path."""
    registry_dir = base / "registry"
    runner.invoke(app, ["registry", "init", "--pool-home", str(registry_dir),
                        "--id", "reg-01", "--context", "Test registry"])
    runner.invoke(app, ["pool", "init", "--registry", "reg-01",
                        "--name", "test-pool", "--context", "Test pool",
                        "--cwd", str(registry_dir)])
    return registry_dir / "test-pool"


def test_registry_init_creates_home_directory_and_global_entry(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry init creates the home directory and writes the entry to the global config."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "init",
        "--pool-home", str(tmp_path / "registry"),
        "--id", "reg-01",
        "--context", "Personal context pools",
    ])
    assert result.exit_code == 0
    # Home dir is created but has no config.yaml.
    assert (tmp_path / "registry").is_dir()
    assert not (tmp_path / "registry" / "config.yaml").exists()
    # Global config has the registry entry.
    global_config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert "reg-01" in global_config["registries"]
    assert global_config["registries"]["reg-01"]["context"] == "Personal context pools"


def test_pool_init_creates_pool_structure(tmp_path: Path) -> None:
    """alph pool init creates snapshots/ and live/ directories."""
    reg_dir = tmp_path / "reg"
    runner.invoke(app, ["registry", "init", "--pool-home", str(reg_dir),
                        "--id", "r1", "--context", "Test"])
    result = runner.invoke(app, [
        "pool", "init",
        "--registry", "r1",
        "--name", "vehicles",
        "--context", "Vehicle maintenance",
        "--cwd", str(reg_dir),
    ])
    assert result.exit_code == 0
    pool = reg_dir / "vehicles"
    assert (pool / "snapshots").is_dir()
    assert (pool / "live").is_dir()


def test_pool_list_shows_registered_pools(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph pool list shows all pools registered under a registry."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    reg_dir = tmp_path / "reg"
    runner.invoke(app, [
        "registry", "init", "--pool-home", str(reg_dir), "--id", "home", "--context", "Home",
    ])
    runner.invoke(app, [
        "pool", "init", "--registry", "home", "--name", "vehicles",
        "--context", "Vehicles", "--cwd", str(tmp_path),
    ])
    runner.invoke(app, [
        "pool", "init", "--registry", "home", "--name", "appliances",
        "--context", "Appliances", "--cwd", str(tmp_path),
    ])
    result = runner.invoke(app, ["pool", "list", "--registry", "home", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert "home" in result.output
    assert "vehicles" in result.output
    assert "appliances" in result.output


def test_pool_list_errors_when_registry_not_found(tmp_path: Path) -> None:
    """alph pool list exits non-zero when registry ID is unknown."""
    result = runner.invoke(app, ["pool", "list", "--registry", "ghost", "--cwd", str(tmp_path)])
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_pool_init_errors_when_registry_not_found(tmp_path: Path) -> None:
    """alph pool init exits non-zero with a helpful message when registry ID is unknown."""
    result = runner.invoke(app, [
        "pool", "init",
        "--registry", "ghost-registry",
        "--name", "vehicles",
        "--context", "Vehicles",
        "--cwd", str(tmp_path),
    ])
    assert result.exit_code != 0
    assert "ghost-registry" in result.output


def test_registry_init_output_mentions_home_and_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry init output shows the registry ID, home dir, and global config path."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "init",
        "--pool-home", str(tmp_path / "reg"),
        "--id", "my-reg",
        "--context", "Test",
        "--name", "My Registry",
    ])
    assert result.exit_code == 0
    assert "my-reg" in result.output
    # Output should mention both the home directory and the config file location.
    assert "pool home" in result.output or str(tmp_path / "reg") in result.output
    assert "config.yaml" in result.output


def test_registry_init_reports_set_as_default(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry init reports when the new registry is set as default."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "init",
        "--pool-home", str(tmp_path / "reg"),
        "--id", "my-reg",
        "--context", "Test",
    ])
    assert result.exit_code == 0
    assert "default" in result.output.lower()


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
    runner.invoke(app, ["add", "-c", "Tire rotation",
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
    """-s archived shows only archived nodes, not active ones."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Active node",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    runner.invoke(app, ["add", "-c", "Archived node", "--status", "archived",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool), "-s", "archived"])
    assert result.exit_code == 0
    assert "Active node" not in result.output
    assert "Archived node" in result.output


def test_list_comma_separated_status_filter(tmp_path: Path) -> None:
    """-s archived,suppressed shows only archived and suppressed, not active."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "Active node",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    runner.invoke(app, ["add", "-c", "Archived node", "--status", "archived",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    runner.invoke(app, ["add", "-c", "Suppressed node", "--status", "suppressed",
                        "--pool", str(pool), "--creator", "chase@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool), "-s", "archived,suppressed"])
    assert result.exit_code == 0
    assert "Active node" not in result.output
    assert "Archived node" in result.output
    assert "Suppressed node" in result.output


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


def test_list_output_json(tmp_path: Path) -> None:
    """alph list -o json emits a JSON array of node objects."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "JSON node", "--pool", str(pool), "--creator", "c@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool), "-o", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["context"] == "JSON node"
    assert "id" in data[0]
    assert "type" in data[0]
    assert "status" in data[0]
    assert "timestamp" in data[0]


def test_list_output_yaml(tmp_path: Path) -> None:
    """alph list -o yaml emits YAML."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "YAML node", "--pool", str(pool), "--creator", "c@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool), "-o", "yaml"])
    assert result.exit_code == 0
    data = yaml.safe_load(result.output)
    assert isinstance(data, list)
    assert data[0]["context"] == "YAML node"


def test_list_output_csv(tmp_path: Path) -> None:
    """alph list -o csv emits CSV with header row."""
    pool = _init_registry_and_pool(tmp_path)
    runner.invoke(app, ["add", "-c", "CSV node", "--pool", str(pool), "--creator", "c@example.com"])
    result = runner.invoke(app, ["list", "--pool", str(pool), "-o", "csv"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0] == "id,type,status,context,timestamp"
    assert "CSV node" in lines[1]


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


# ---------------------------------------------------------------------------
# Registry list
# ---------------------------------------------------------------------------


def test_registry_list_shows_registry_id_and_context(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry list displays registry ID and context from the global config."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    registry_dir = tmp_path / "my-registry"
    runner.invoke(app, [
        "registry", "init",
        "--pool-home", str(registry_dir),
        "--id", "personal",
        "--context", "Personal context pools",
        "--name", "Personal",
    ])
    result = runner.invoke(app, ["registry", "list"])
    assert result.exit_code == 0
    assert "personal" in result.output
    assert "Personal context pools" in result.output


def test_registry_list_shows_no_registries_when_none_exist(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry list reports no registries when none are declared in the config tree."""
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(tmp_path / "empty-global"))
    result = runner.invoke(app, ["registry", "list", "--cwd", str(tmp_path / "nowhere")])
    assert result.exit_code == 0
    assert "no registries" in result.output.lower()


def test_pool_init_shows_known_registries_on_unknown_registry_error(tmp_path: Path) -> None:
    """alph pool init prints known registries when the specified registry is not found."""
    registry_dir = tmp_path / "reg"
    runner.invoke(app, [
        "registry", "init",
        "--pool-home", str(registry_dir),
        "--id", "existing-reg",
        "--context", "Existing registry",
    ])
    result = runner.invoke(app, [
        "pool", "init",
        "--registry", "ghost-registry",
        "--name", "vehicles",
        "--context", "Vehicles",
        "--cwd", str(registry_dir),
    ])
    assert result.exit_code != 0
    assert "ghost-registry" in result.output
    assert "existing-reg" in result.output


# ---------------------------------------------------------------------------
# alph config list and alph config show
# ---------------------------------------------------------------------------


def test_config_list_shows_global_config_path(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config list includes the global config path in the output."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["config", "list", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    # Rich table may truncate long paths; check that the distinctive directory name appears.
    assert global_dir.resolve().name in result.output


def test_config_list_shows_cwd_config_path(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config list includes the cwd config.yaml path in the output."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["config", "list", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    # Walk-up from cwd produces local config entries; verify at least one appears.
    assert "local" in result.output


def test_config_list_marks_existing_config_files(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config list distinguishes existing from missing config files."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {"creator": "test@example.com"})
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["config", "list", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    # The output should have some marker for existing vs missing files
    assert "exists" in result.output.lower() or "missing" in result.output.lower()


def test_config_show_displays_existing_config_content(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config show displays the YAML content of an existing config file."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {"creator": "test@example.com"})
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    config_path = global_dir / "config.yaml"
    result = runner.invoke(app, ["config", "show", str(config_path)])
    assert result.exit_code == 0
    assert "creator" in result.output


def test_config_show_prints_bootstrap_notice_for_missing_file(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config show explains how to bootstrap when the config file does not exist."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    config_path = global_dir / "config.yaml"
    result = runner.invoke(app, ["config", "show", str(config_path)])
    assert result.exit_code == 0
    # Should mention the file is missing and show a template / bootstrap hint
    output_lower = result.output.lower()
    assert "not found" in output_lower or "does not exist" in output_lower or "bootstrap" in output_lower


def test_config_show_bootstrap_notice_includes_template_keys(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config show on a missing file prints not-found message and hints."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    config_path = global_dir / "config.yaml"
    result = runner.invoke(app, ["config", "show", str(config_path)])
    assert result.exit_code == 0
    assert "not found" in result.output
    assert "registry init" in result.output
    assert "alph defaults" in result.output


def test_defaults_shows_configured_values(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph defaults shows the resolved creator, registry, pool, and auto_commit."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    reg_dir = tmp_path / "reg"
    runner.invoke(app, [
        "registry", "init", "--pool-home", str(reg_dir), "--id", "home", "--context", "Home",
    ])
    runner.invoke(app, [
        "pool", "init", "--registry", "home", "--name", "vehicles",
        "--context", "Vehicles", "--cwd", str(tmp_path),
    ])
    result = runner.invoke(app, ["defaults", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert "home" in result.output
    assert "vehicles" in result.output


def test_defaults_shows_not_set_when_unconfigured(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph defaults shows 'not set' placeholders when no config exists."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["defaults", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert "not set" in result.output


# ---------------------------------------------------------------------------
# Remote registry CLI integration
# ---------------------------------------------------------------------------


def _mock_provider() -> MagicMock:
    """Create a mock provider that returns one snapshot node."""
    provider = MagicMock()
    provider.list_files.side_effect = [
        [FileEntry(name="abc123def456.md", path="reg/vehicles/snapshots/abc123def456.md", file_type="blob")],
        [],  # live/ empty
    ]
    provider.read_files.return_value = {
        "reg/vehicles/snapshots/abc123def456.md": (
            "---\n"
            "schema_version: '1'\n"
            "id: abc123def456\n"
            "timestamp: '2024-01-01T00:00:00+00:00'\n"
            "source: cli\n"
            "node_type: snapshot\n"
            "context: Test node.\n"
            "creator: test@example.com\n"
            "status: active\n"
            "---\n"
        ),
    }
    return provider


def test_list_remote_pool_via_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph list with a remote git URL resolves via provider."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_prov = _mock_provider()
    with patch("alph.cli.provider_for_url", return_value=mock_prov):
        result = runner.invoke(app, [
            "list",
            "--pool", "git@github.com:org/repo.git:/reg/vehicles",
        ])
    assert result.exit_code == 0
    assert "abc123def456" in result.output


def test_show_remote_pool_via_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph show with a remote git URL resolves via provider."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_prov = _mock_provider()
    with patch("alph.cli.provider_for_url", return_value=mock_prov):
        result = runner.invoke(app, [
            "show", "abc123def456",
            "--pool", "git@github.com:org/repo.git:/reg/vehicles",
        ])
    assert result.exit_code == 0
    assert "Test node." in result.output


def test_validate_remote_pool_via_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph validate with a remote git URL resolves via provider."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_prov = _mock_provider()
    with patch("alph.cli.provider_for_url", return_value=mock_prov):
        result = runner.invoke(app, [
            "validate",
            "--pool", "git@github.com:org/repo.git:/reg/vehicles",
        ])
    assert result.exit_code == 0
    assert "valid" in result.output


def test_add_remote_ro_pool_errors(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph add against a remote RO pool exits with error."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "add", "-c", "Should fail.",
        "--pool", "git@github.com:org/repo.git:/reg/vehicles",
        "--creator", "test@example.com",
    ])
    assert result.exit_code != 0
    assert "read-only" in result.output


def test_list_remote_default_pool(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph list resolves default pool from a remote registry."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "default_registry": "remote-reg",
        "default_pool": "vehicles",
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/reg",
                "context": "Remote test.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_prov = _mock_provider()
    with patch("alph.cli.provider_for_url", return_value=mock_prov):
        result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "abc123def456" in result.output


def test_registry_list_shows_mode_column(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry list shows mode column (ro for remote, rw for local)."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "local-reg": {
                "pool_home": str(tmp_path / "local"),
                "context": "Local.",
            },
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "list", "--cwd", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert "rw" in result.output
    assert "ro" in result.output


def test_registry_check_local_exists(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry check on existing local registry reports ok."""
    pool_home = tmp_path / "registry"
    pool_home.mkdir()
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "local-reg": {
                "pool_home": str(pool_home),
                "context": "Local test.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "check", "local-reg", "--cwd", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert "ok" in result.output


def test_registry_check_local_missing(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry check on missing local registry reports error."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "local-reg": {
                "pool_home": str(tmp_path / "nonexistent"),
                "context": "Missing.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "check", "local-reg", "--cwd", str(tmp_path),
    ])
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_registry_check_unknown_id(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry check on unknown ID reports error with known registries."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "real-reg": {
                "pool_home": str(tmp_path),
                "context": "Real.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "check", "ghost", "--cwd", str(tmp_path),
    ])
    assert result.exit_code != 0
    assert "not found" in result.output
    assert "real-reg" in result.output
