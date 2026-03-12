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


def test_registry_init_suppresses_defaults_reminder_when_disabled(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    """alph registry init omits the 'not set as default' hint when defaults_reminder: false."""
    global_dir = tmp_path / "global"
    global_dir.mkdir(parents=True)
    # Pre-seed a config with a default already set and defaults_reminder off.
    (global_dir / "config.yaml").write_text(
        "default_registry: existing\ndefaults_reminder: false\n"
    )
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "init",
        "--pool-home", str(tmp_path / "reg"),
        "--id", "my-reg",
        "--context", "Test",
    ])
    assert result.exit_code == 0
    assert "to make it default" not in result.output
    assert "not set as default" not in result.output


def test_registry_init_shows_defaults_reminder_by_default(
    tmp_path: Path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    """alph registry init shows the 'not set as default' hint unless suppressed."""
    global_dir = tmp_path / "global"
    global_dir.mkdir(parents=True)
    # Pre-seed a config with a default already set but no defaults_reminder key.
    (global_dir / "config.yaml").write_text("default_registry: existing\n")
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "init",
        "--pool-home", str(tmp_path / "reg"),
        "--id", "my-reg",
        "--context", "Test",
    ])
    assert result.exit_code == 0
    assert "to make it default" in result.output


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


def test_add_remote_rw_pool_matches_correct_registry(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph add against RW remote finds the RW entry, not an RO entry for the same URL."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    pool_in_clone = clone_dir / "data" / "vehicles"
    (pool_in_clone / "snapshots").mkdir(parents=True)
    (pool_in_clone / "live").mkdir(parents=True)
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "creator": "test@example.com",
        "registries": {
            "ro-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Read-only.",
                "mode": "ro",
            },
            "rw-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Read-write.",
                "mode": "rw",
                "clone_path": str(clone_dir),
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    with patch("alph.cli.clone_remote_registry", return_value=True):
        result = runner.invoke(app, [
            "add", "-c", "Should succeed via RW entry.",
            "--pool", "git@github.com:org/repo.git:/data/vehicles",
            "--creator", "test@example.com",
        ])
    assert result.exit_code == 0, f"Expected success but got: {result.output}"
    assert "node created" in result.output


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


def test_list_remote_uses_branch_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph list passes configured branch to resolve_pool_readonly."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "default_registry": "remote-reg",
        "default_pool": "vehicles",
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/reg",
                "context": "Remote test.",
                "branch": "seeded",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_prov = _mock_provider()
    with patch("alph.cli.provider_for_url", return_value=mock_prov), \
            patch("alph.cli.resolve_pool_readonly") as mock_resolve:
        # Set up the context manager mock to yield a real pool path.
        pool_dir = tmp_path / "pool"
        (pool_dir / "snapshots").mkdir(parents=True)
        (pool_dir / "live").mkdir(parents=True)
        mock_resolve.return_value.__enter__ = lambda s: pool_dir
        mock_resolve.return_value.__exit__ = lambda s, *a: None
        runner.invoke(app, ["list"])
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args.kwargs
    assert call_kwargs.get("ref") == "seeded"


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


# ---------------------------------------------------------------------------
# Registry clone and pull commands
# ---------------------------------------------------------------------------


def test_registry_clone_calls_git_clone(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry clone creates a local clone of a remote registry."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
                "mode": "rw",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("alph.remote.subprocess.run", return_value=mock_result):
        result = runner.invoke(app, [
            "registry", "clone", "remote-reg",
            "--clone-path", str(tmp_path / "clone"),
            "--cwd", str(tmp_path),
        ])
    assert result.exit_code == 0
    assert "cloned" in result.output


def test_registry_clone_already_exists(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry clone shows 'already cloned' when clone exists."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
                "mode": "rw",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "clone", "remote-reg",
        "--clone-path", str(clone_dir),
        "--cwd", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert "already cloned" in result.output


def test_registry_clone_errors_for_local(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry clone errors when the registry is local."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "local-reg": {
                "pool_home": str(tmp_path / "local"),
                "context": "Local.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "clone", "local-reg", "--cwd", str(tmp_path),
    ])
    assert result.exit_code != 0
    assert "local registry" in result.output


def test_registry_clone_errors_for_unknown(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry clone errors for unknown registry ID."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {"registries": {}})
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "clone", "ghost", "--cwd", str(tmp_path),
    ])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_registry_pull_calls_git_pull(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry pull pulls latest changes for a cloned remote registry."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
                "mode": "rw",
                "clone_path": str(clone_dir),
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("alph.remote.subprocess.run", return_value=mock_result):
        result = runner.invoke(app, [
            "registry", "pull", "remote-reg", "--cwd", str(tmp_path),
        ])
    assert result.exit_code == 0
    assert "pulled" in result.output


def test_registry_pull_errors_no_clone(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry pull errors when no clone exists."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
                "clone_path": str(tmp_path / "no-such-clone"),
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "pull", "remote-reg", "--cwd", str(tmp_path),
    ])
    assert result.exit_code != 0
    assert "no clone found" in result.output


def test_registry_pull_errors_for_local(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry pull errors when the registry is local."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "local-reg": {
                "pool_home": str(tmp_path / "local"),
                "context": "Local.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "pull", "local-reg", "--cwd", str(tmp_path),
    ])
    assert result.exit_code != 0
    assert "local registry" in result.output


# ---------------------------------------------------------------------------
# registry status
# ---------------------------------------------------------------------------


def test_registry_status_remote_cloned(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry status shows full info for a cloned remote registry."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
                "mode": "rw",
                "clone_path": str(clone_dir),
                "branch": "seeded",
                "auto_push": True,
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    with patch("subprocess.run", return_value=mock_result):
        result = runner.invoke(app, [
            "registry", "status", "remote-reg", "--cwd", str(tmp_path),
        ])
    assert result.exit_code == 0
    assert "remote-reg" in result.output
    assert "rw" in result.output
    assert "git@github.com:org/repo.git" in result.output
    assert "data" in result.output
    assert "seeded" in result.output
    assert "cloned (clean)" in result.output
    assert "auto_push:   true" in result.output


def test_registry_status_remote_not_cloned(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry status shows 'not cloned' when no local clone exists."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "status", "remote-reg", "--cwd", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert "not cloned" in result.output
    assert "ro" in result.output


def test_registry_status_local(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry status shows path and exists for local registries."""
    pool_home = tmp_path / "registry"
    pool_home.mkdir()
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "local-reg": {
                "pool_home": str(pool_home),
                "context": "Local.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "status", "local-reg", "--cwd", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert "local-reg" in result.output
    assert "rw" in result.output
    assert "path:" in result.output
    assert "exists:" in result.output
    assert "true" in result.output


def test_registry_status_unknown(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry status errors for unknown registry."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {"registries": {}})
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "registry", "status", "nope", "--cwd", str(tmp_path),
    ])
    assert result.exit_code != 0
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# RW clone-based pool context
# ---------------------------------------------------------------------------


def test_add_remote_rw_pool_uses_clone(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph add against an RW remote pool uses the local clone."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    # Create a fake clone with pool structure.
    pool_in_clone = clone_dir / "data" / "vehicles"
    (pool_in_clone / "snapshots").mkdir(parents=True)
    (pool_in_clone / "live").mkdir(parents=True)
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "creator": "test@example.com",
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
                "mode": "rw",
                "clone_path": str(clone_dir),
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    # Mock clone_remote_registry to be a no-op (clone already exists).
    with patch("alph.cli.clone_remote_registry", return_value=True):
        result = runner.invoke(app, [
            "add", "-c", "Test node via RW.",
            "--pool", "git@github.com:org/repo.git:/data/vehicles",
            "--creator", "test@example.com",
        ])
    assert result.exit_code == 0
    assert "node created" in result.output
    # Node should be written to the clone.
    nodes = list((pool_in_clone / "snapshots").glob("*.md"))
    assert len(nodes) == 1


# ---------------------------------------------------------------------------
# --pull flag
# ---------------------------------------------------------------------------


def test_list_pull_flag_triggers_pull(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph list --pull triggers a git pull for RW clones."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    pool_in_clone = clone_dir / "data" / "vehicles"
    (pool_in_clone / "snapshots").mkdir(parents=True)
    (pool_in_clone / "live").mkdir(parents=True)
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote.",
                "mode": "rw",
                "clone_path": str(clone_dir),
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_pull_result = MagicMock()
    mock_pull_result.returncode = 0
    with patch("alph.cli.clone_remote_registry", return_value=True), \
            patch("alph.cli.pull_remote_registry") as mock_pull:
        result = runner.invoke(app, [
            "list",
            "--pool", "git@github.com:org/repo.git:/data/vehicles",
            "--pull",
        ])
    assert result.exit_code == 0
    mock_pull.assert_called_once()


# ---------------------------------------------------------------------------
# --registry global option
# ---------------------------------------------------------------------------


def test_global_registry_option_with_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph --registry <url> list --pool <name> resolves to remote pool."""
    global_dir = tmp_path / "global"
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_prov = _mock_provider()
    with patch("alph.cli.provider_for_url", return_value=mock_prov):
        result = runner.invoke(app, [
            "--registry", "git@github.com:org/repo.git:/reg",
            "list", "--pool", "vehicles",
        ])
    assert result.exit_code == 0
    assert "abc123def456" in result.output


def test_global_registry_option_with_id(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph --registry <id> list --pool <name> resolves pool from that registry."""
    global_dir = tmp_path / "global"
    reg_dir = tmp_path / "reg"
    pool = reg_dir / "vehicles"
    (pool / "snapshots").mkdir(parents=True)
    (pool / "live").mkdir(parents=True)
    _write_global_config(global_dir, {
        "registries": {
            "my-reg": {
                "pool_home": str(reg_dir),
                "context": "Test.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "--registry", "my-reg",
        "list", "--pool", "vehicles",
    ])
    assert result.exit_code == 0


def test_registry_override_ro_when_rw_shares_url(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """--registry <ro-id> uses RO API path even when an RW registry shares the URL."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "ro-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Read-only.",
                "mode": "ro",
                "branch": "seeded",
            },
            "rw-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Read-write.",
                "mode": "rw",
                "clone_path": str(tmp_path / "clone"),
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_prov = _mock_provider()
    with patch("alph.cli.provider_for_url", return_value=mock_prov), \
            patch("alph.cli.resolve_pool_readonly") as mock_resolve:
        pool_dir = tmp_path / "pool"
        (pool_dir / "snapshots").mkdir(parents=True)
        (pool_dir / "live").mkdir(parents=True)
        mock_resolve.return_value.__enter__ = lambda s: pool_dir
        mock_resolve.return_value.__exit__ = lambda s, *a: None
        result = runner.invoke(app, [
            "--registry", "ro-reg",
            "list", "--pool", "vehicles",
        ])
    assert result.exit_code == 0, f"Expected success but got: {result.output}"
    # Must use resolve_pool_readonly (RO API), not clone_remote_registry (RW).
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args.kwargs
    assert call_kwargs.get("ref") == "seeded"


def test_adhoc_registry_url_does_not_match_configured_rw(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """--registry <raw-url> uses ephemeral RO, ignoring configured RW with same URL."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "registries": {
            "rw-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Read-write.",
                "mode": "rw",
                "clone_path": str(clone_dir),
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    mock_prov = _mock_provider()
    with patch("alph.cli.provider_for_url", return_value=mock_prov), \
            patch("alph.cli.resolve_pool_readonly") as mock_resolve, \
            patch("alph.cli.clone_remote_registry") as mock_clone:
        pool_dir = tmp_path / "pool"
        (pool_dir / "snapshots").mkdir(parents=True)
        (pool_dir / "live").mkdir(parents=True)
        mock_resolve.return_value.__enter__ = lambda s: pool_dir
        mock_resolve.return_value.__exit__ = lambda s, *a: None
        result = runner.invoke(app, [
            "--registry", "git@github.com:org/repo.git:/data",
            "list", "--pool", "vehicles",
        ])
    assert result.exit_code == 0, f"Expected success but got: {result.output}"
    # Must use RO API path, not the RW clone.
    mock_resolve.assert_called_once()
    mock_clone.assert_not_called()
    # Default ref is HEAD (no branch config for ad-hoc URL).
    call_kwargs = mock_resolve.call_args.kwargs
    assert call_kwargs.get("ref") == "HEAD"


def test_auto_pull_triggers_pull_on_rw_read(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """auto_pull: true triggers a pull before reading from an RW clone."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    pool_dir = clone_dir / "data" / "vehicles"
    (pool_dir / "snapshots").mkdir(parents=True)
    (pool_dir / "live").mkdir(parents=True)
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "default_registry": "rw-reg",
        "default_pool": "vehicles",
        "registries": {
            "rw-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Read-write.",
                "mode": "rw",
                "clone_path": str(clone_dir),
                "auto_pull": True,
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    with patch("alph.cli.clone_remote_registry", return_value=False), \
            patch("alph.cli.pull_remote_registry") as mock_pull:
        result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, f"Expected success but got: {result.output}"
    mock_pull.assert_called_once_with(clone_dir, ssh_command="")


def test_no_auto_pull_when_explicitly_disabled(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """With auto_pull explicitly false, reading from RW clone does not pull."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    pool_dir = clone_dir / "data" / "vehicles"
    (pool_dir / "snapshots").mkdir(parents=True)
    (pool_dir / "live").mkdir(parents=True)
    (clone_dir / ".git").mkdir(parents=True)
    _write_global_config(global_dir, {
        "default_registry": "rw-reg",
        "default_pool": "vehicles",
        "registries": {
            "rw-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Read-write.",
                "mode": "rw",
                "clone_path": str(clone_dir),
                "auto_pull": False,
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    with patch("alph.cli.clone_remote_registry", return_value=False), \
            patch("alph.cli.pull_remote_registry") as mock_pull:
        result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, f"Expected success but got: {result.output}"
    mock_pull.assert_not_called()


def test_global_registry_option_unknown_errors(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph --registry <unknown> list errors."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {"registries": {}})
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, [
        "--registry", "ghost",
        "list", "--pool", "vehicles",
    ])
    assert result.exit_code != 0
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# alph registry/pool default to list
# ---------------------------------------------------------------------------


def test_registry_no_subcommand_defaults_to_list(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry (no subcommand) shows the registry list."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "test-reg": {
                "pool_home": str(tmp_path / "data"),
                "context": "Test.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["registry"])
    assert result.exit_code == 0
    assert "test-reg" in result.output


def test_pool_no_subcommand_defaults_to_list(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph pool (no subcommand) shows the pool list."""
    global_dir = tmp_path / "global"
    pool_dir = tmp_path / "data" / "vehicles"
    (pool_dir / "snapshots").mkdir(parents=True)
    (pool_dir / "live").mkdir(parents=True)
    _write_global_config(global_dir, {
        "default_registry": "test-reg",
        "registries": {
            "test-reg": {
                "pool_home": str(tmp_path / "data"),
                "context": "Test.",
                "pools": {"vehicles": {"context": "Cars."}},
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["pool"])
    assert result.exit_code == 0
    assert "vehicles" in result.output


# ---------------------------------------------------------------------------
# alph registry check all
# ---------------------------------------------------------------------------


def test_registry_check_all_checks_every_registry(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry check all checks every known registry."""
    global_dir = tmp_path / "global"
    (tmp_path / "local1").mkdir()
    (tmp_path / "local2").mkdir()
    _write_global_config(global_dir, {
        "registries": {
            "reg-a": {
                "pool_home": str(tmp_path / "local1"),
                "context": "A.",
            },
            "reg-b": {
                "pool_home": str(tmp_path / "local2"),
                "context": "B.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["registry", "check", "all"])
    assert result.exit_code == 0
    assert "reg-a" in result.output
    assert "reg-b" in result.output


# ---------------------------------------------------------------------------
# Registry commands default to default_registry
# ---------------------------------------------------------------------------


def test_registry_check_uses_default_registry_when_no_arg(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry check with no argument uses default_registry."""
    global_dir = tmp_path / "global"
    (tmp_path / "local").mkdir()
    _write_global_config(global_dir, {
        "default_registry": "local-reg",
        "registries": {
            "local-reg": {
                "pool_home": str(tmp_path / "local"),
                "context": "Local test.",
            },
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["registry", "check"])
    assert result.exit_code == 0
    assert "local-reg" in result.output


def test_registry_check_errors_when_no_arg_no_default(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry check with no argument and no default_registry prints error."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "registries": {
            "reg-a": {"pool_home": str(tmp_path / "local"), "context": "A."},
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["registry", "check"])
    assert result.exit_code == 1
    assert "default_registry" in result.output


def test_registry_status_uses_default_registry_when_no_arg(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph registry status with no argument uses default_registry."""
    global_dir = tmp_path / "global"
    (tmp_path / "local").mkdir()
    _write_global_config(global_dir, {
        "default_registry": "loc",
        "registries": {
            "loc": {"pool_home": str(tmp_path / "local"), "context": "Test."},
        },
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["registry", "status"])
    assert result.exit_code == 0
    assert "loc" in result.output


# ---------------------------------------------------------------------------
# alph config check — unknown key detection
# ---------------------------------------------------------------------------


def test_config_check_clean_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config check reports no warnings for a valid config."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "creator": "a@b.com",
        "default_registry": "r",
        "registries": {"r": {"pool_home": "/p", "context": "c"}},
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["config", "check", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert "no issues" in result.output.lower() or "ok" in result.output.lower()


def test_config_check_detects_unknown_key(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config check flags unknown keys."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "creator": "a@b.com",
        "bogus_option": True,
        "registries": {"r": {"pool_home": "/p", "context": "c", "clone_dir": "/x"}},
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["config", "check", "--cwd", str(tmp_path)])
    assert result.exit_code == 1
    assert "bogus_option" in result.output
    assert "clone_dir" in result.output


# ---------------------------------------------------------------------------
# alph config show-all — display merged config with defaults
# ---------------------------------------------------------------------------


def test_config_show_all_displays_merged_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """alph config show-all outputs the merged config with all defaults filled in."""
    global_dir = tmp_path / "global"
    _write_global_config(global_dir, {
        "creator": "a@b.com",
        "registries": {"r1": {"pool_home": "/p", "context": "c"}},
    })
    monkeypatch.setenv("ALPH_CONFIG_DIR", str(global_dir))
    result = runner.invoke(app, ["config", "show-all", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert "auto_commit" in result.output
    assert "default_registry" in result.output
    assert "auto_push" in result.output
    assert "auto_pull" in result.output


# ---------------------------------------------------------------------------
# alph examples
# ---------------------------------------------------------------------------


def test_examples_command_runs_and_shows_content() -> None:
    """alph examples prints structured usage walkthroughs."""
    result = runner.invoke(app, ["examples"])
    assert result.exit_code == 0
    assert "Getting started" in result.output
    assert "registry init" in result.output
    assert "pool init" in result.output
    assert "alph add" in result.output
