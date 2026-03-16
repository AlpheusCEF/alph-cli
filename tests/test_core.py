"""Behavior tests for alph core logic."""

import subprocess
from pathlib import Path

import yaml

from alph.core import (
    RESERVED_NAMES,
    AlphConfig,
    HydrationConfig,
    HydrationTypeConfig,
    NodeDetail,
    RegistryEntry,
    RemoteRegistryRef,
    _VALID_CONTENT_TYPES,
    check_git_state,
    check_idempotency,
    collect_registries,
    create_node,
    default_global_config_text,
    extract_frontmatter,
    find_registry_config,
    find_registry_for_pool,
    generate_id,
    init_pool,
    init_registry,
    is_remote_registry,
    list_config_paths,
    list_nodes,
    list_pools,
    load_config,
    load_hydration_config,
    load_state,
    parse_remote_registry,
    resolve_default_pool,
    resolve_pool_name,
    show_node,
    update_node,
    update_state,
    validate_node,
)


def _make_pool(base: Path) -> Path:
    """Create a minimal pool directory structure."""
    pool = base / "my-pool"
    (pool / "snapshots").mkdir(parents=True)
    (pool / "live").mkdir(parents=True)
    return pool


def _write_node(directory: Path, node_id: str, creator: str, timestamp: str) -> None:
    """Write a minimal node file into a pool subdirectory."""
    content = (
        f"---\nschema_version: '1'\nid: {node_id}\ntimestamp: '{timestamp}'\n"
        f"source: cli\nnode_type: snapshot\ncontext: test node\ncreator: {creator}\n---\n"
    )
    (directory / f"{node_id}.md").write_text(content)


def test_check_idempotency_returns_none_for_empty_pool(tmp_path: Path) -> None:
    """check_idempotency returns None when no node with that ID exists."""
    pool = _make_pool(tmp_path)
    assert check_idempotency(pool, "a1b2c3d4e5f6") is None


def test_check_idempotency_finds_node_in_snapshots(tmp_path: Path) -> None:
    """check_idempotency returns existing node metadata when ID is found in snapshots/."""
    pool = _make_pool(tmp_path)
    _write_node(pool / "snapshots", "a1b2c3d4e5f6", "chase@example.com", "2026-03-05T10:00:00Z")
    result = check_idempotency(pool, "a1b2c3d4e5f6")
    assert result is not None
    assert result.creator == "chase@example.com"
    assert result.timestamp == "2026-03-05T10:00:00Z"


def test_check_idempotency_finds_node_in_pointers(tmp_path: Path) -> None:
    """check_idempotency returns existing node metadata when ID is found in pointers/."""
    pool = _make_pool(tmp_path)
    _write_node(pool / "live", "a1b2c3d4e5f6", "chase@example.com", "2026-03-05T10:00:00Z")
    result = check_idempotency(pool, "a1b2c3d4e5f6")
    assert result is not None
    assert result.creator == "chase@example.com"


def _write_config(path: Path, content: dict[str, object]) -> None:
    """Write a YAML config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(content))


def test_load_state_returns_empty_state_when_no_file_exists(tmp_path: Path) -> None:
    """load_state returns a default TimelineState when no state file exists."""
    pool = _make_pool(tmp_path)
    state = load_state(pool)
    assert state.last_loaded is None
    assert state.node_verified == {}


def test_update_state_writes_and_load_state_reads_it_back(tmp_path: Path) -> None:
    """update_state persists state; load_state reads it back accurately."""
    pool = _make_pool(tmp_path)
    state = load_state(pool)
    updated = update_state(pool, state, last_loaded="2026-03-05T10:00:00Z",
                           node_verified={"a1b2c3d4e5f6": "2026-03-05T10:00:00Z"})
    reloaded = load_state(pool)
    assert reloaded.last_loaded == "2026-03-05T10:00:00Z"
    assert reloaded.node_verified == {"a1b2c3d4e5f6": "2026-03-05T10:00:00Z"}
    # update_state should return the new state
    assert updated.last_loaded == "2026-03-05T10:00:00Z"


def test_list_nodes_returns_empty_for_empty_pool(tmp_path: Path) -> None:
    """list_nodes returns an empty list when the pool has no nodes."""
    pool = _make_pool(tmp_path)
    assert list_nodes(pool) == []


def test_list_nodes_returns_summary_for_each_node(tmp_path: Path) -> None:
    """list_nodes returns one NodeSummary per node file in the pool."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Oil change", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="live",
                context="Jira ticket AUTH-123", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z")
    summaries = list_nodes(pool)
    assert len(summaries) == 2
    contexts = {s.context for s in summaries}
    assert "Oil change" in contexts
    assert "Jira ticket AUTH-123" in contexts


def test_list_nodes_summary_has_expected_fields(tmp_path: Path) -> None:
    """Each NodeSummary from list_nodes has id, context, node_type, and timestamp."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Brake pads at 40%", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    summaries = list_nodes(pool)
    s = summaries[0]
    assert s.node_id
    assert s.context == "Brake pads at 40%"
    assert s.node_type == "snapshot"
    assert s.timestamp


def test_show_node_returns_full_content_by_id(tmp_path: Path) -> None:
    """show_node returns the full NodeDetail for a matching node ID."""
    pool = _make_pool(tmp_path)
    result = create_node(pool_path=pool, source="cli", node_type="snapshot",
                         context="Oil change at Valvoline", creator="chase@example.com",
                         timestamp="2026-03-05T10:00:00Z", content="Full synthetic 0W-20.")
    detail = show_node(pool, result.node_id)
    assert detail is not None
    assert detail.node_id == result.node_id
    assert detail.context == "Oil change at Valvoline"
    assert "Full synthetic 0W-20." in detail.body


def test_show_node_returns_none_for_unknown_id(tmp_path: Path) -> None:
    """show_node returns None when no node with that ID exists."""
    pool = _make_pool(tmp_path)
    assert show_node(pool, "nonexistent1") is None


def test_init_registry_creates_registry_entry_in_global_config(tmp_path: Path) -> None:
    """init_registry writes the registry declaration into the global config, not the home dir."""
    global_dir = tmp_path / "global"
    result = init_registry(
        pool_home=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Personal context pools",
        global_config_dir=global_dir,
    )
    # config_path points to the global config, not the home dir.
    assert result.config_path == global_dir / "config.yaml"
    assert result.config_path.exists()
    config = yaml.safe_load(result.config_path.read_text())
    entry = config["registries"]["reg-01"]
    assert entry["context"] == "Personal context pools"
    assert entry["pool_home"] == str(tmp_path / "my-registry")
    # No config.yaml is written inside the pool_home directory.
    assert not (tmp_path / "my-registry" / "config.yaml").exists()


def test_init_registry_creates_default_hydration_yaml(tmp_path: Path) -> None:
    """init_registry creates a starter hydration.yaml with barrel defaults for local registries."""
    global_dir = tmp_path / "global"
    pool_home = tmp_path / "my-registry"
    init_registry(
        pool_home=pool_home,
        registry_id="reg-01",
        context="Test",
        global_config_dir=global_dir,
    )
    hydration_file = pool_home / "hydration.yaml"
    assert hydration_file.exists()
    data = yaml.safe_load(hydration_file.read_text())
    assert "barrel" in data
    assert data["barrel"]["default_ttl"] == "4h"
    assert "types" in data


def test_init_registry_does_not_overwrite_existing_hydration_yaml(tmp_path: Path) -> None:
    """init_registry preserves an existing hydration.yaml."""
    global_dir = tmp_path / "global"
    pool_home = tmp_path / "my-registry"
    pool_home.mkdir(parents=True)
    existing = pool_home / "hydration.yaml"
    existing.write_text("types:\n  custom:\n    provider: my-provider\n")
    init_registry(
        pool_home=pool_home,
        registry_id="reg-01",
        context="Test",
        global_config_dir=global_dir,
    )
    # Should not overwrite
    data = yaml.safe_load(existing.read_text())
    assert "custom" in data["types"]
    assert "barrel" not in data  # original had no barrel section


def test_init_registry_skips_hydration_yaml_for_remote(tmp_path: Path) -> None:
    """init_registry does not create hydration.yaml for remote registries."""
    global_dir = tmp_path / "global"
    init_registry(
        pool_home=Path("git@github.com:org/repo.git"),
        registry_id="remote-01",
        context="Remote",
        global_config_dir=global_dir,
    )
    # No local directory to write to — just check no crash


def test_init_registry_validates_its_own_output(tmp_path: Path) -> None:
    """init_registry result passes registry validation."""
    global_dir = tmp_path / "global"
    result = init_registry(
        pool_home=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Personal context pools",
        global_config_dir=global_dir,
    )
    assert result.valid is True


def test_init_registry_sets_default_when_no_existing_default(tmp_path: Path) -> None:
    """init_registry writes default_registry and registry entry into the global config."""
    global_dir = tmp_path / "global"
    result = init_registry(
        pool_home=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Test registry",
        global_config_dir=global_dir,
    )
    assert result.set_as_default is True
    global_config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert global_config["default_registry"] == "reg-01"
    assert "reg-01" in global_config["registries"]


def test_init_registry_does_not_override_existing_default(tmp_path: Path) -> None:
    """init_registry does not change default_registry when one already exists."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {"default_registry": "existing"})
    result = init_registry(
        pool_home=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Test registry",
        global_config_dir=global_dir,
    )
    assert result.set_as_default is False
    global_config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert global_config["default_registry"] == "existing"
    # Registry entry still gets registered even when not the default.
    assert "reg-01" in global_config["registries"]


def test_find_registry_config_finds_by_id_in_cfg(tmp_path: Path) -> None:
    """find_registry_config returns (actual_id, home_path) when registry ID is in cfg."""
    cfg = AlphConfig(registries={"reg-01": RegistryEntry(pool_home=str(tmp_path / "home"))})
    result = find_registry_config("reg-01", cfg=cfg)
    assert result is not None
    actual_id, home = result
    assert actual_id == "reg-01"
    assert home == tmp_path / "home"


def test_find_registry_config_finds_by_name_from_cfg(tmp_path: Path) -> None:
    """find_registry_config matches on registry name from cfg — no file I/O needed."""
    reg_home = tmp_path / "home"
    cfg = AlphConfig(registries={"reg-01": RegistryEntry(pool_home=str(reg_home), name="My Registry")})
    result = find_registry_config("My Registry", cfg=cfg)
    assert result is not None
    actual_id, home = result
    assert actual_id == "reg-01"
    assert home == reg_home


def test_find_registry_config_returns_none_when_not_in_cfg(tmp_path: Path) -> None:
    """find_registry_config returns None when the ID is not in cfg.registries."""
    cfg = AlphConfig(registries={"other-reg": RegistryEntry(pool_home=str(tmp_path))})
    assert find_registry_config("nonexistent", cfg=cfg) is None


def test_find_registry_config_returns_none_for_empty_cfg(tmp_path: Path) -> None:
    """find_registry_config returns None when cfg has no registries."""
    cfg = AlphConfig()
    assert find_registry_config("reg-01", cfg=cfg) is None


# ---------------------------------------------------------------------------
# collect_registries
# ---------------------------------------------------------------------------


def test_collect_registries_returns_summaries_from_cfg(tmp_path: Path) -> None:
    """collect_registries returns one RegistrySummary per entry in cfg.registries."""
    reg_home = tmp_path / "home"
    cfg = AlphConfig(registries={
        "reg-01": RegistryEntry(pool_home=str(reg_home), context="My context", name="My Registry"),
    })
    summaries = collect_registries(cfg=cfg)
    assert len(summaries) == 1
    assert summaries[0].registry_id == "reg-01"
    assert summaries[0].name == "My Registry"
    assert summaries[0].context == "My context"


def test_collect_registries_returns_empty_when_no_registries(tmp_path: Path) -> None:
    """collect_registries returns an empty list when cfg has no registries."""
    cfg = AlphConfig()
    assert collect_registries(cfg=cfg) == []


def test_collect_registries_home_path_points_to_registry_home_dir(tmp_path: Path) -> None:
    """collect_registries reports home_path as the registry home directory."""
    reg_home = tmp_path / "home"
    cfg = AlphConfig(registries={"reg-01": RegistryEntry(pool_home=str(reg_home), context="Test")})
    summaries = collect_registries(cfg=cfg)
    assert summaries[0].home_path == reg_home


def test_init_pool_creates_required_directories(tmp_path: Path) -> None:
    """init_pool creates snapshots/ and live/ inside the pool."""
    global_dir = tmp_path / "global"
    init_registry(pool_home=tmp_path, registry_id="reg-01", context="Test registry",
                  global_config_dir=global_dir)
    result = init_pool(
        registry_id="reg-01",
        name="highlander",
        context="Maintenance for the Highlander",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    assert (result.pool_path / "snapshots").is_dir()
    assert (result.pool_path / "live").is_dir()


def test_init_pool_registers_pool_in_global_config(tmp_path: Path) -> None:
    """init_pool adds the pool entry to the global config under the registry entry."""
    global_dir = tmp_path / "global"
    init_registry(pool_home=tmp_path, registry_id="reg-01", context="Test registry",
                  global_config_dir=global_dir)
    # Enable config registration so pool entry is written.
    cfg_path = global_dir / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["register_subdir_pools"] = True
    cfg_path.write_text(yaml.dump(data, sort_keys=False))
    result = init_pool(
        registry_id="reg-01",
        name="highlander",
        context="Maintenance for the Highlander",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    # config_path points to the global config.
    assert result.config_path == global_dir / "config.yaml"
    config = yaml.safe_load(result.config_path.read_text())
    pools = config["registries"]["reg-01"]["pools"]
    assert "highlander" in pools
    assert pools["highlander"]["context"] == "Maintenance for the Highlander"


def test_init_pool_validates_its_own_output(tmp_path: Path) -> None:
    """init_pool result passes registry validation."""
    global_dir = tmp_path / "global"
    init_registry(pool_home=tmp_path, registry_id="reg-01", context="Test registry",
                  global_config_dir=global_dir)
    result = init_pool(
        registry_id="reg-01",
        name="highlander",
        context="Maintenance for the Highlander",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    assert result.valid is True


def test_init_pool_errors_when_registry_not_found(tmp_path: Path) -> None:
    """init_pool returns an invalid result when the registry ID is not found."""
    result = init_pool(
        registry_id="nonexistent",
        name="vehicles",
        context="Vehicles",
        cwd=tmp_path,
        global_config_dir=tmp_path / "global",
    )
    assert result.valid is False
    assert any("nonexistent" in e for e in result.errors)


def test_init_pool_bootstrap_creates_registry_and_pool(tmp_path: Path) -> None:
    """init_pool with bootstrap=True creates the registry if not found."""
    result = init_pool(
        registry_id="new-reg",
        name="vehicles",
        context="Vehicles",
        cwd=tmp_path,
        global_config_dir=tmp_path / "global",
        bootstrap=True,
        registry_context="Bootstrapped registry",
    )
    assert result.valid is True
    assert (result.pool_path / "snapshots").is_dir()


def test_init_pool_writes_default_pool_to_global_config(tmp_path: Path) -> None:
    """init_pool sets default_pool in the global config when this is the default registry."""
    global_dir = tmp_path / "global"
    init_registry(pool_home=tmp_path, registry_id="reg-01", context="Test",
                  global_config_dir=global_dir)
    init_pool(
        registry_id="reg-01",
        name="vehicles",
        context="Vehicles",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    global_config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert global_config.get("default_pool") == "vehicles"


def test_init_pool_does_not_override_existing_default_pool(tmp_path: Path) -> None:
    """init_pool does not overwrite default_pool when one is already set."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "default_registry": "reg-01",
        "default_pool": "existing-pool",
        "registries": {"reg-01": {"pool_home": str(tmp_path), "context": "Test"}},
    })
    init_pool(
        registry_id="reg-01",
        name="new-pool",
        context="New pool",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    global_config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert global_config["default_pool"] == "existing-pool"


def test_list_pools_includes_configured_pools(tmp_path: Path) -> None:
    """list_pools returns pools declared in the config."""
    global_dir = tmp_path / "global"
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "home": {
                "pool_home": str(reg_dir),
                "context": "Home",
                "pools": {
                    "vehicles": {"context": "Cars.", "type": "subdir"},
                },
            },
        },
    })
    cfg = load_config(global_config_dir=global_dir)
    result = list_pools("home", cfg=cfg)
    assert result is not None
    assert len(result) == 1
    assert result[0].name == "vehicles"
    assert result[0].source == "configured"


def test_list_pools_discovers_unconfigured_pools_on_disk(tmp_path: Path) -> None:
    """list_pools discovers pools that exist on disk but are not in the config."""
    global_dir = tmp_path / "global"
    reg_dir = tmp_path / "registry"
    (reg_dir / "vehicles" / "snapshots").mkdir(parents=True)
    (reg_dir / "appliances" / "live").mkdir(parents=True)
    (reg_dir / "not-a-pool").mkdir(parents=True)  # no snapshots/ or live/
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "home": {
                "pool_home": str(reg_dir),
                "context": "Home",
                "pools": {
                    "vehicles": {"context": "Cars.", "type": "subdir"},
                },
            },
        },
    })
    cfg = load_config(global_config_dir=global_dir)
    result = list_pools("home", cfg=cfg)
    assert result is not None
    names = {s.name: s.source for s in result}
    assert names["vehicles"] == "configured"
    assert names["appliances"] == "discovered"
    assert "not-a-pool" not in names


def test_list_pools_no_duplicate_for_configured_on_disk(tmp_path: Path) -> None:
    """list_pools does not duplicate a pool that is both configured and on disk."""
    global_dir = tmp_path / "global"
    reg_dir = tmp_path / "registry"
    (reg_dir / "vehicles" / "snapshots").mkdir(parents=True)
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "home": {
                "pool_home": str(reg_dir),
                "context": "Home",
                "pools": {
                    "vehicles": {"context": "Cars.", "type": "subdir"},
                },
            },
        },
    })
    cfg = load_config(global_config_dir=global_dir)
    result = list_pools("home", cfg=cfg)
    assert result is not None
    vehicle_entries = [s for s in result if s.name == "vehicles"]
    assert len(vehicle_entries) == 1
    assert vehicle_entries[0].source == "configured"


def test_list_pools_returns_none_for_unknown_registry(tmp_path: Path) -> None:
    """list_pools returns None when registry is not found."""
    cfg = load_config(global_config_dir=tmp_path / "global")
    assert list_pools("ghost", cfg=cfg) is None


def test_load_config_returns_defaults_when_no_files_exist(tmp_path: Path) -> None:
    """load_config returns an AlphConfig with defaults when no config files are present."""
    config = load_config(global_config_dir=tmp_path / "global")
    assert isinstance(config, AlphConfig)
    assert config.auto_commit is False


def test_load_config_reads_global_creator(tmp_path: Path) -> None:
    """load_config picks up creator email from the global config file."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {"creator": "chase@example.com"})
    config = load_config(global_config_dir=global_dir)
    assert config.creator == "chase@example.com"


def test_load_config_falls_back_to_system_username_when_no_creator(tmp_path: Path) -> None:
    """load_config uses the system username when creator is not set in any config."""
    global_dir = tmp_path / "global"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text("{}\n")
    config = load_config(global_config_dir=global_dir)
    # Should be a non-empty string from the OS
    assert config.creator != ""
    assert isinstance(config.creator, str)


def test_load_config_cwd_local_overrides_global(tmp_path: Path) -> None:
    """A config.yaml in cwd overrides global config for the same key."""
    global_dir = tmp_path / "global"
    cwd = tmp_path / "project"
    _write_config(global_dir / "config.yaml", {"creator": "global@example.com", "auto_commit": False})
    _write_config(cwd / "config.yaml", {"creator": "local@example.com"})
    config = load_config(global_config_dir=global_dir, cwd=cwd)
    assert config.creator == "local@example.com"
    assert config.auto_commit is False


def test_load_config_parent_dir_config_is_picked_up(tmp_path: Path) -> None:
    """load_config reads a config.yaml in a parent directory of cwd."""
    global_dir = tmp_path / "global"
    project = tmp_path / "project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    _write_config(global_dir / "config.yaml", {"creator": "global@example.com"})
    _write_config(project / "config.yaml", {"creator": "project@example.com"})
    config = load_config(global_config_dir=global_dir, cwd=nested)
    assert config.creator == "project@example.com"


def test_load_config_cwd_most_specific_wins_over_parent(tmp_path: Path) -> None:
    """When two cwd-walk configs set the same key, the one closest to cwd wins."""
    global_dir = tmp_path / "global"
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    _write_config(global_dir / "config.yaml", {"creator": "global@example.com"})
    _write_config(parent / "config.yaml", {"creator": "parent@example.com"})
    _write_config(child / "config.yaml", {"creator": "child@example.com"})
    config = load_config(global_config_dir=global_dir, cwd=child)
    assert config.creator == "child@example.com"


def test_load_config_cli_overrides_override_everything(tmp_path: Path) -> None:
    """CLI overrides take precedence over both global and local configs."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {"creator": "global@example.com"})
    config = load_config(
        global_config_dir=global_dir,
        overrides={"creator": "cli@example.com"},
    )
    assert config.creator == "cli@example.com"


def test_load_config_accumulates_registries_from_global_and_cwd(tmp_path: Path) -> None:
    """Registries from global and cwd walk configs are merged."""
    global_dir = tmp_path / "global"
    cwd = tmp_path / "project"
    _write_config(global_dir / "config.yaml", {
        "registries": {"household": "/registries/household"},
    })
    _write_config(cwd / "config.yaml", {
        "registries": {"work": "/registries/work"},
    })
    config = load_config(global_config_dir=global_dir, cwd=cwd)
    assert "household" in config.registries
    assert "work" in config.registries
    assert config.registries["household"].pool_home == "/registries/household"
    assert config.registries["work"].pool_home == "/registries/work"


def test_load_config_cwd_registry_entry_overrides_global_for_same_id(tmp_path: Path) -> None:
    """A cwd-local registry entry for the same ID overrides the global one."""
    global_dir = tmp_path / "global"
    cwd = tmp_path / "project"
    _write_config(global_dir / "config.yaml", {
        "registries": {"household": "/global/household"},
    })
    _write_config(cwd / "config.yaml", {
        "registries": {"household": "/local/household"},
    })
    config = load_config(global_config_dir=global_dir, cwd=cwd)
    assert config.registries["household"].pool_home == "/local/household"


def test_load_config_picks_up_registry_from_local_config_in_cwd_walk(tmp_path: Path) -> None:
    """load_config walking cwd finds a local config with a dict-format registry entry."""
    global_dir = tmp_path / "global"
    project = tmp_path / "project"
    project.mkdir()
    # A local config written with the new dict format (pool_home key explicit).
    _write_config(project / "config.yaml", {
        "registries": {"reg-01": {"pool_home": str(tmp_path / "reg-home"), "context": "Test"}},
    })
    config = load_config(global_config_dir=global_dir, cwd=project)
    assert "reg-01" in config.registries
    assert config.registries["reg-01"].pool_home == str(tmp_path / "reg-home")
    assert config.registries["reg-01"].context == "Test"


def test_resolve_default_pool_returns_path_when_configured(tmp_path: Path) -> None:
    """resolve_default_pool returns registry_home/pool_name when both are configured."""
    registry_path = tmp_path / "registry"
    config = AlphConfig(
        default_registry="household",
        default_pool="vehicles",
        registries={"household": RegistryEntry(pool_home=str(registry_path))},
    )
    assert resolve_default_pool(config) == registry_path / "vehicles"


def test_resolve_default_pool_returns_none_when_no_default_registry(tmp_path: Path) -> None:
    """resolve_default_pool returns None when default_registry is not set."""
    config = AlphConfig(default_pool="vehicles")
    assert resolve_default_pool(config) is None


def test_resolve_default_pool_returns_none_when_registry_not_in_map(tmp_path: Path) -> None:
    """resolve_default_pool returns None when the default registry ID isn't in registries."""
    config = AlphConfig(default_registry="household", default_pool="vehicles")
    assert resolve_default_pool(config) is None


def test_resolve_default_pool_returns_none_when_no_default_pool(tmp_path: Path) -> None:
    """resolve_default_pool returns None when default_pool is not set."""
    config = AlphConfig(
        default_registry="household",
        registries={"household": RegistryEntry(pool_home="/registries/household")},
    )
    assert resolve_default_pool(config) is None


def test_resolve_pool_name_finds_pool_in_default_registry(tmp_path: Path) -> None:
    """resolve_pool_name returns registry_home/name when pool exists in the default registry."""
    reg_home = tmp_path / "registry"
    config = AlphConfig(
        default_registry="household",
        registries={
            "household": RegistryEntry(
                pool_home=str(reg_home),
                pools={"vehicles": {"context": "Cars", "type": "subdir"}},
            )
        },
    )
    assert resolve_pool_name("vehicles", config) == reg_home / "vehicles"


def test_resolve_pool_name_finds_pool_by_directory_when_not_in_pools_dict(tmp_path: Path) -> None:
    """resolve_pool_name finds a pool that exists on disk even without a pools: entry."""
    reg_home = tmp_path / "registry"
    pool_dir = reg_home / "vehicles"
    pool_dir.mkdir(parents=True)
    config = AlphConfig(
        default_registry="household",
        registries={
            "household": RegistryEntry(pool_home=str(reg_home)),  # no pools: key
        },
    )
    assert resolve_pool_name("vehicles", config) == pool_dir


def test_resolve_pool_name_returns_none_when_not_found(tmp_path: Path) -> None:
    """resolve_pool_name returns None when no registry has the named pool."""
    reg_home = tmp_path / "registry"
    config = AlphConfig(
        default_registry="household",
        registries={
            "household": RegistryEntry(pool_home=str(reg_home), pools={"vehicles": {"context": "Cars"}}),
        },
    )
    assert resolve_pool_name("appliances", config) is None


def test_resolve_pool_name_checks_default_registry_first(tmp_path: Path) -> None:
    """resolve_pool_name prefers the default registry when the name exists in multiple."""
    reg_a = tmp_path / "reg-a"
    reg_b = tmp_path / "reg-b"
    config = AlphConfig(
        default_registry="reg-b",
        registries={
            "reg-a": RegistryEntry(pool_home=str(reg_a), pools={"tools": {"context": "A tools"}}),
            "reg-b": RegistryEntry(pool_home=str(reg_b), pools={"tools": {"context": "B tools"}}),
        },
    )
    assert resolve_pool_name("tools", config) == reg_b / "tools"


def test_init_pool_stores_type_not_layout(tmp_path: Path) -> None:
    """init_pool stores 'type' (not 'layout') and omits 'path' in pool metadata."""
    global_dir = tmp_path / "global"
    init_registry(pool_home=tmp_path, registry_id="reg-01", context="Test", global_config_dir=global_dir)
    # Enable config registration so pool entry is written.
    cfg_path = global_dir / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["register_subdir_pools"] = True
    cfg_path.write_text(yaml.dump(data, sort_keys=False))
    init_pool(
        registry_id="reg-01",
        name="vehicles",
        context="Cars",
        pool_type="subdir",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    config = yaml.safe_load((global_dir / "config.yaml").read_text())
    pool_meta = config["registries"]["reg-01"]["pools"]["vehicles"]
    assert pool_meta["type"] == "subdir"
    assert "layout" not in pool_meta
    assert "path" not in pool_meta


def test_create_node_auto_commits_when_auto_commit_is_true(tmp_path: Path) -> None:
    """create_node makes a git commit when auto_commit=True and pool is in a git repo."""
    pool = _make_pool(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=tmp_path, check=True, capture_output=True)

    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Auto-committed node",
        creator="test@example.com",
        auto_commit=True,
    )

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    )
    assert f"alph: add snapshot node {result.node_id}" in log.stdout


def test_create_node_does_not_commit_when_auto_commit_is_false(tmp_path: Path) -> None:
    """create_node does not create a git commit when auto_commit=False (default)."""
    pool = _make_pool(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=tmp_path, check=True, capture_output=True)

    create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Not auto-committed",
        creator="test@example.com",
        auto_commit=False,
    )

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path,
        capture_output=True, text=True,
    )
    assert log.returncode != 0  # no commits in repo


def test_create_node_writes_fixed_node_to_snapshots(tmp_path: Path) -> None:
    """create_node writes a fixed node file into snapshots/ and returns its path and ID."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Oil change at Valvoline, full synthetic",
        creator="chase@example.com",
    )
    assert result.node_id is not None
    assert result.path.parent == pool / "snapshots"
    assert result.path.exists()


def test_create_node_writes_live_node_to_pointers(tmp_path: Path) -> None:
    """create_node writes a live node file into pointers/."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="live",
        context="Jira ticket AUTH-123",
        creator="chase@example.com",
    )
    assert result.path.parent == pool / "live"


def test_create_node_frontmatter_is_valid(tmp_path: Path) -> None:
    """The file created by create_node has valid frontmatter that passes validate_node."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Brake pads at 40%, replace by 110k",
        creator="chase@example.com",
    )
    frontmatter = extract_frontmatter(result.path.read_text())
    assert frontmatter is not None
    validation = validate_node(frontmatter)
    assert validation.valid is True


def test_create_node_timestamp_stored_as_string(tmp_path: Path) -> None:
    """create_node stores the timestamp as a quoted string, not a datetime object."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Some context",
        creator="chase@example.com",
    )
    frontmatter = extract_frontmatter(result.path.read_text())
    assert frontmatter is not None
    assert isinstance(frontmatter["timestamp"], str)


def test_create_node_returns_duplicate_error_when_node_exists(tmp_path: Path) -> None:
    """create_node returns a duplicate error when an identical node already exists."""
    pool = _make_pool(tmp_path)
    kwargs = dict(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Oil change at Valvoline",
        creator="chase@example.com",
        timestamp="2026-03-05T10:00:00Z",
    )
    create_node(**kwargs)  # type: ignore[arg-type]
    result = create_node(**kwargs)  # type: ignore[arg-type]
    assert result.duplicate is True
    assert result.existing_creator == "chase@example.com"


def test_create_node_deduplicates_without_explicit_timestamp(tmp_path: Path) -> None:
    """Calling create_node twice with the same source+context but no timestamp deduplicates.

    This is the real-world CLI case: no timestamp is supplied, so datetime.now()
    is called each time. The ID must still match because timestamp is not part
    of the identity hash.
    """
    pool = _make_pool(tmp_path)
    kwargs = dict(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Purchased 2022 Subaru Outback Wilderness",
        creator="test@example.com",
    )
    first = create_node(**kwargs)  # type: ignore[arg-type]
    second = create_node(**kwargs)  # type: ignore[arg-type]
    assert second.duplicate is True
    assert second.node_id == first.node_id
    assert second.existing_creator == "test@example.com"


def test_generate_id_returns_12_char_hex() -> None:
    """generate_id returns a 12-character lowercase hex string."""
    node_id = generate_id(source="cli", context="Oil change at Valvoline")
    assert len(node_id) == 12
    assert all(c in "0123456789abcdef" for c in node_id)


def test_generate_id_is_deterministic() -> None:
    """generate_id returns the same ID for the same inputs."""
    kwargs = {"source": "cli", "context": "Oil change"}
    assert generate_id(**kwargs) == generate_id(**kwargs)


def test_generate_id_differs_for_different_inputs() -> None:
    """generate_id returns different IDs for different context values."""
    id1 = generate_id(source="cli", context="Oil change")
    id2 = generate_id(source="cli", context="Brake check")
    assert id1 != id2


def test_generate_id_is_stable_across_source_versions() -> None:
    """generate_id produces the same ID regardless of version suffix in source.

    A UA-style source like 'alph-cli/v0.1.24' must hash identically to
    'alph-cli/v0.1.99' so that re-adding the same context after an upgrade
    still triggers the duplicate check.
    """
    id1 = generate_id(source="alph-cli/v0.1.24", context="Oil change")
    id2 = generate_id(source="alph-cli/v0.1.99", context="Oil change")
    assert id1 == id2


def test_generate_id_still_differs_for_different_source_base() -> None:
    """generate_id distinguishes different source types even with version stripped."""
    id1 = generate_id(source="alph-cli/v0.1.24", context="Oil change")
    id2 = generate_id(source="alph-mcp/v0.1.24", context="Oil change")
    assert id1 != id2


def test_extract_frontmatter_returns_parsed_yaml() -> None:
    """extract_frontmatter returns a dict of YAML fields from a markdown file."""
    text = "---\nschema_version: '1'\ncontext: Oil change\n---\nSome body text."
    result = extract_frontmatter(text)
    assert result == {"schema_version": "1", "context": "Oil change"}


def test_extract_frontmatter_returns_none_when_no_delimiters() -> None:
    """extract_frontmatter returns None when the file has no frontmatter."""
    result = extract_frontmatter("Just plain text, no frontmatter.")
    assert result is None


def test_extract_frontmatter_returns_none_for_empty_string() -> None:
    """extract_frontmatter returns None for an empty string."""
    assert extract_frontmatter("") is None


def test_valid_node_passes_validation() -> None:
    """A well-formed fixed node with all required fields passes validation."""
    node = {
        "schema_version": "1",
        "id": "a1b2c3d4e5f6",
        "timestamp": "2026-03-05T10:00:00Z",
        "source": "cli",
        "node_type": "snapshot",
        "context": "Oil change at Valvoline, full synthetic",
        "creator": "chase@example.com",
    }
    result = validate_node(node)
    assert result.valid is True
    assert result.errors == []


def test_node_missing_required_fields_fails_validation() -> None:
    """A node missing required fields reports each missing field as an error."""
    result = validate_node({})
    assert result.valid is False
    assert "missing required field: 'schema_version'" in result.errors
    assert "missing required field: 'context'" in result.errors
    assert len(result.errors) == 7


def test_node_invalid_node_type_fails_validation() -> None:
    """A node with an unrecognised node_type fails validation."""
    node = {
        "schema_version": "1",
        "id": "a1b2c3d4e5f6",
        "timestamp": "2026-03-05T10:00:00Z",
        "source": "cli",
        "node_type": "bogus",
        "context": "Some context",
        "creator": "chase@example.com",
    }
    result = validate_node(node)
    assert result.valid is False
    assert any("node_type" in e for e in result.errors)


def test_node_invalid_schema_version_fails_validation() -> None:
    """A node with an unsupported schema_version fails validation."""
    node = {
        "schema_version": "99",
        "id": "a1b2c3d4e5f6",
        "timestamp": "2026-03-05T10:00:00Z",
        "source": "cli",
        "node_type": "snapshot",
        "context": "Some context",
        "creator": "chase@example.com",
    }
    result = validate_node(node)
    assert result.valid is False
    assert any("schema_version" in e for e in result.errors)


def test_live_node_passes_validation() -> None:
    """A well-formed live node passes validation."""
    node = {
        "schema_version": "1",
        "id": "a1b2c3d4e5f6",
        "timestamp": "2026-03-05T10:00:00Z",
        "source": "cli",
        "node_type": "live",
        "context": "Jira ticket for auth migration",
        "creator": "chase@example.com",
    }
    result = validate_node(node)
    assert result.valid is True
    assert result.errors == []


# ---------------------------------------------------------------------------
# Status field
# ---------------------------------------------------------------------------


def _base_node(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": "1",
        "id": "a1b2c3d4e5f6",
        "timestamp": "2026-03-05T10:00:00Z",
        "source": "cli",
        "node_type": "snapshot",
        "context": "Oil change",
        "creator": "chase@example.com",
    }
    base.update(overrides)
    return base


def test_node_without_status_passes_validation() -> None:
    """A node without a status field is valid — active is the implicit default."""
    result = validate_node(_base_node())
    assert result.valid is True


def test_node_with_explicit_active_status_passes_validation() -> None:
    """A node with status: active is explicitly valid."""
    result = validate_node(_base_node(status="active"))
    assert result.valid is True


def test_node_with_archived_status_passes_validation() -> None:
    """A node with status: archived passes validation."""
    result = validate_node(_base_node(status="archived"))
    assert result.valid is True


def test_node_with_suppressed_status_passes_validation() -> None:
    """A node with status: suppressed passes validation."""
    result = validate_node(_base_node(status="suppressed"))
    assert result.valid is True


def test_node_with_invalid_status_fails_validation() -> None:
    """A node with an unrecognised status value fails validation."""
    result = validate_node(_base_node(status="deleted"))
    assert result.valid is False
    assert any("status" in e for e in result.errors)


def test_create_node_writes_status_to_frontmatter(tmp_path: Path) -> None:
    """create_node writes the status field to frontmatter when provided."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Archived maintenance note",
        creator="chase@example.com",
        status="archived",
    )
    frontmatter = extract_frontmatter(result.path.read_text())
    assert frontmatter is not None
    assert frontmatter["status"] == "archived"


def test_create_node_omits_status_from_frontmatter_when_not_provided(tmp_path: Path) -> None:
    """create_node does not write a status field when none is given — active is implicit."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Normal node",
        creator="chase@example.com",
    )
    frontmatter = extract_frontmatter(result.path.read_text())
    assert frontmatter is not None
    assert "status" not in frontmatter


def test_list_nodes_excludes_archived_by_default(tmp_path: Path) -> None:
    """list_nodes omits archived nodes from default results."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Active node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Archived node", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="archived")
    summaries = list_nodes(pool)
    contexts = {s.context for s in summaries}
    assert "Active node" in contexts
    assert "Archived node" not in contexts


def test_list_nodes_excludes_suppressed_by_default(tmp_path: Path) -> None:
    """list_nodes omits suppressed nodes from default results."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Active node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Suppressed node", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="suppressed")
    summaries = list_nodes(pool)
    contexts = {s.context for s in summaries}
    assert "Active node" in contexts
    assert "Suppressed node" not in contexts


def test_list_nodes_includes_archived_when_requested(tmp_path: Path) -> None:
    """list_nodes includes archived nodes when include_statuses contains archived."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Active node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Archived node", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="archived")
    summaries = list_nodes(pool, include_statuses={"active", "archived"})
    contexts = {s.context for s in summaries}
    assert "Active node" in contexts
    assert "Archived node" in contexts


def test_list_nodes_includes_all_statuses_when_all_requested(tmp_path: Path) -> None:
    """list_nodes returns every node when include_statuses contains all three values."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Active node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Archived node", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="archived")
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Suppressed node", creator="chase@example.com",
                timestamp="2026-03-05T12:00:00Z", status="suppressed")
    summaries = list_nodes(pool, include_statuses={"active", "archived", "suppressed"})
    assert len(summaries) == 3


def test_node_summary_exposes_status(tmp_path: Path) -> None:
    """NodeSummary from list_nodes includes the status field."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Archived node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z", status="archived")
    summaries = list_nodes(pool, include_statuses={"active", "archived"})
    assert summaries[0].status == "archived"


def test_node_summary_status_defaults_to_active_when_absent(tmp_path: Path) -> None:
    """NodeSummary reports status as active when frontmatter has no status field."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="snapshot",
                context="Normal node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    summaries = list_nodes(pool)
    assert summaries[0].status == "active"


# ---------------------------------------------------------------------------
# list_config_paths and default_global_config_text
# ---------------------------------------------------------------------------


def test_list_config_paths_always_includes_global(tmp_path: Path) -> None:
    """list_config_paths always includes the global config path."""
    global_dir = tmp_path / "global"
    summaries = list_config_paths(global_config_dir=global_dir, cwd=tmp_path)
    paths = [s.path for s in summaries]
    assert global_dir / "config.yaml" in paths


def test_list_config_paths_includes_cwd(tmp_path: Path) -> None:
    """list_config_paths includes cwd/config.yaml in the results."""
    global_dir = tmp_path / "global"
    summaries = list_config_paths(global_config_dir=global_dir, cwd=tmp_path)
    paths = [s.path for s in summaries]
    assert tmp_path / "config.yaml" in paths


def test_list_config_paths_marks_existing_files(tmp_path: Path) -> None:
    """list_config_paths marks exists=True only for files that are on disk."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {"creator": "test@example.com"})
    summaries = list_config_paths(global_config_dir=global_dir, cwd=tmp_path)
    global_s = next(s for s in summaries if s.is_global)
    cwd_s = next(s for s in summaries if s.path == tmp_path / "config.yaml")
    assert global_s.exists is True
    assert cwd_s.exists is False


def test_list_config_paths_marks_global(tmp_path: Path) -> None:
    """list_config_paths marks the global config entry with is_global=True."""
    global_dir = tmp_path / "global"
    summaries = list_config_paths(global_config_dir=global_dir, cwd=tmp_path)
    global_count = sum(1 for s in summaries if s.is_global)
    assert global_count == 1
    assert next(s for s in summaries if s.is_global).path == global_dir / "config.yaml"


def test_list_config_paths_shows_registry_ids_from_existing_file(tmp_path: Path) -> None:
    """list_config_paths includes registry IDs declared in each existing config file."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {"my-reg": str(tmp_path / "home")},
    })
    summaries = list_config_paths(global_config_dir=global_dir, cwd=tmp_path)
    global_s = next(s for s in summaries if s.is_global)
    assert "my-reg" in global_s.registry_ids


def test_list_config_paths_non_global_cwd_not_marked_global(tmp_path: Path) -> None:
    """A cwd config.yaml is not marked is_global even when it exists."""
    global_dir = tmp_path / "global"
    _write_config(tmp_path / "config.yaml", {"creator": "local@example.com"})
    summaries = list_config_paths(global_config_dir=global_dir, cwd=tmp_path)
    cwd_s = next(s for s in summaries if s.path == tmp_path / "config.yaml")
    assert cwd_s.is_global is False
    assert cwd_s.exists is True


def test_default_global_config_text_contains_all_standard_keys() -> None:
    """default_global_config_text() returns text with all standard config keys."""
    text = default_global_config_text()
    for key in ("creator", "default_registry", "default_pool", "registries", "auto_commit"):
        assert key in text


def test_default_global_config_text_contains_comments() -> None:
    """default_global_config_text() has at least one # comment per config key."""
    text = default_global_config_text()
    assert text.count("#") >= 5


def test_default_global_config_text_is_valid_yaml() -> None:
    """default_global_config_text() produces valid YAML that parses without error."""
    text = default_global_config_text()
    # Strip comment lines before parsing (pyyaml handles inline comments but
    # let's confirm the overall structure is valid)
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Remote registry detection and parsing
# ---------------------------------------------------------------------------


def test_is_remote_registry_ssh_git_at() -> None:
    """SSH git@ URLs are detected as remote."""
    assert is_remote_registry("git@github.com:AlpheusCEF/repo.git:/registry") is True


def test_is_remote_registry_ssh_no_subpath() -> None:
    """SSH git@ URL without subpath is detected as remote."""
    assert is_remote_registry("git@github.com:AlpheusCEF/repo.git") is True


def test_is_remote_registry_https_with_dotgit() -> None:
    """HTTPS URL with .git suffix is detected as remote."""
    assert is_remote_registry("https://github.com/AlpheusCEF/repo.git:/registry") is True


def test_is_remote_registry_https_without_dotgit() -> None:
    """HTTPS URL without .git suffix is detected as remote."""
    assert is_remote_registry("https://github.com/AlpheusCEF/repo") is True


def test_is_remote_registry_ssh_protocol() -> None:
    """ssh:// URLs are detected as remote."""
    assert is_remote_registry("ssh://git@github.com/AlpheusCEF/repo.git") is True


def test_is_remote_registry_git_protocol() -> None:
    """git:// URLs are detected as remote."""
    assert is_remote_registry("git://github.com/AlpheusCEF/repo.git") is True


def test_is_remote_registry_local_absolute_path() -> None:
    """Absolute local paths are not remote."""
    assert is_remote_registry("/tmp/alph-test/registry") is False


def test_is_remote_registry_local_relative_path() -> None:
    """Relative local paths are not remote."""
    assert is_remote_registry("./my-registry") is False


def test_is_remote_registry_local_home_path() -> None:
    """Home-relative paths are not remote."""
    assert is_remote_registry("~/registries/household") is False


def test_is_remote_registry_empty_string() -> None:
    """Empty string is not remote."""
    assert is_remote_registry("") is False


def test_parse_remote_registry_ssh_with_subpath() -> None:
    """SSH URL with subpath parses correctly and returns RemoteRegistryRef."""
    ref = parse_remote_registry("git@github.com:AlpheusCEF/repo.git:/registry")
    assert isinstance(ref, RemoteRegistryRef)
    assert ref.remote_url == "git@github.com:AlpheusCEF/repo.git"
    assert ref.subpath == "registry"
    assert ref.original == "git@github.com:AlpheusCEF/repo.git:/registry"


def test_parse_remote_registry_ssh_no_subpath() -> None:
    """SSH URL without subpath parses with empty subpath."""
    ref = parse_remote_registry("git@github.com:AlpheusCEF/repo.git")
    assert ref.remote_url == "git@github.com:AlpheusCEF/repo.git"
    assert ref.subpath == ""


def test_parse_remote_registry_https_with_subpath() -> None:
    """HTTPS URL with subpath parses correctly."""
    ref = parse_remote_registry("https://github.com/AlpheusCEF/repo.git:/registry/sub")
    assert ref.remote_url == "https://github.com/AlpheusCEF/repo.git"
    assert ref.subpath == "registry/sub"


def test_parse_remote_registry_https_no_dotgit_no_subpath() -> None:
    """HTTPS URL without .git and no subpath."""
    ref = parse_remote_registry("https://github.com/AlpheusCEF/repo")
    assert ref.remote_url == "https://github.com/AlpheusCEF/repo"
    assert ref.subpath == ""


def test_parse_remote_registry_raises_on_local_path() -> None:
    """Parsing a local path raises ValueError."""
    import pytest

    with pytest.raises(ValueError, match="not a remote"):
        parse_remote_registry("/tmp/local/path")


def test_parse_remote_registry_deep_subpath() -> None:
    """Subpath with multiple segments parses correctly."""
    ref = parse_remote_registry("git@gitlab.com:org/repo.git:/a/b/c")
    assert ref.remote_url == "git@gitlab.com:org/repo.git"
    assert ref.subpath == "a/b/c"


def test_parse_remote_registry_strips_leading_slash_from_subpath() -> None:
    """The leading slash after :/ is stripped from subpath."""
    ref = parse_remote_registry("git@github.com:org/repo.git:/registry")
    assert ref.subpath == "registry"
    assert not ref.subpath.startswith("/")


# ---------------------------------------------------------------------------
# RegistryEntry mode field
# ---------------------------------------------------------------------------


def test_registry_entry_mode_defaults_to_empty() -> None:
    """RegistryEntry.mode defaults to empty string (resolved later based on locality)."""
    entry = RegistryEntry(pool_home="/tmp/test")
    assert entry.mode == ""


def test_registry_entry_mode_ro() -> None:
    """RegistryEntry accepts mode='ro'."""
    entry = RegistryEntry(pool_home="git@github.com:org/repo.git", mode="ro")
    assert entry.mode == "ro"


def test_registry_entry_mode_rw() -> None:
    """RegistryEntry accepts mode='rw'."""
    entry = RegistryEntry(pool_home="git@github.com:org/repo.git", mode="rw")
    assert entry.mode == "rw"


def test_load_config_reads_mode_from_yaml(tmp_path: Path) -> None:
    """load_config picks up mode from registry config YAML."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "rw",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].mode == "rw"


def test_load_config_mode_defaults_to_empty_when_omitted(tmp_path: Path) -> None:
    """load_config leaves mode as empty when not specified in YAML."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "local-reg": {
                "pool_home": str(tmp_path / "home"),
                "context": "Local test.",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["local-reg"].mode == ""


def test_effective_mode_remote_defaults_ro() -> None:
    """A remote registry with no explicit mode is effectively ro."""
    from alph.core import effective_mode

    entry = RegistryEntry(pool_home="git@github.com:org/repo.git", mode="")
    assert effective_mode(entry) == "ro"


def test_effective_mode_local_always_rw() -> None:
    """A local registry is always rw regardless of mode field."""
    from alph.core import effective_mode

    entry = RegistryEntry(pool_home="/tmp/local", mode="")
    assert effective_mode(entry) == "rw"


def test_effective_mode_remote_explicit_rw() -> None:
    """A remote registry with explicit mode='rw' returns rw."""
    from alph.core import effective_mode

    entry = RegistryEntry(pool_home="git@github.com:org/repo.git", mode="rw")
    assert effective_mode(entry) == "rw"


def test_effective_mode_local_ignores_ro() -> None:
    """A local registry ignores mode='ro' — local is always rw."""
    from alph.core import effective_mode

    entry = RegistryEntry(pool_home="/tmp/local", mode="ro")
    assert effective_mode(entry) == "rw"


def test_load_config_reads_auto_push_from_yaml(tmp_path: Path) -> None:
    """load_config picks up auto_push from registry config YAML."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "rw",
                "auto_push": True,
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].auto_push is True


def test_load_config_auto_push_defaults_true_for_rw_remote(tmp_path: Path) -> None:
    """load_config defaults auto_push to True for remote RW registries when not explicitly set."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "rw",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].auto_push is True


def test_load_config_auto_push_defaults_false_for_local(tmp_path: Path) -> None:
    """load_config defaults auto_push to False for local registries."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "local-reg": {
                "pool_home": str(tmp_path / "data"),
                "context": "Local test.",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["local-reg"].auto_push is False


def test_load_config_auto_push_defaults_false_for_ro_remote(tmp_path: Path) -> None:
    """load_config defaults auto_push to False for remote RO registries."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "ro",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].auto_push is False


def test_load_config_auto_push_explicit_false_overrides_rw_default(tmp_path: Path) -> None:
    """Explicitly setting auto_push: false on RW remote overrides the smart default."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "rw",
                "auto_push": False,
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].auto_push is False


def test_load_config_reads_clone_path_from_yaml(tmp_path: Path) -> None:
    """load_config picks up clone_path from registry config YAML."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "rw",
                "clone_path": "/tmp/my-clone",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].clone_path == "/tmp/my-clone"


def test_load_config_reads_auto_pull_from_yaml(tmp_path: Path) -> None:
    """load_config picks up auto_pull from registry config YAML."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "auto_pull": True,
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].auto_pull is True


def test_load_config_auto_pull_defaults_true_for_rw_remote(tmp_path: Path) -> None:
    """load_config defaults auto_pull to True for remote RW registries when not explicitly set."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "rw",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].auto_pull is True


def test_load_config_auto_pull_defaults_false_for_local(tmp_path: Path) -> None:
    """load_config defaults auto_pull to False for local registries."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "local-reg": {
                "pool_home": str(tmp_path / "data"),
                "context": "Local test.",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["local-reg"].auto_pull is False


def test_load_config_auto_pull_defaults_false_for_ro_remote(tmp_path: Path) -> None:
    """load_config defaults auto_pull to False for remote RO registries."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "ro",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].auto_pull is False


def test_load_config_auto_pull_explicit_false_overrides_rw_default(tmp_path: Path) -> None:
    """Explicitly setting auto_pull: false on RW remote overrides the smart default."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "mode": "rw",
                "auto_pull": False,
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].auto_pull is False


def test_load_config_reads_branch_from_yaml(tmp_path: Path) -> None:
    """load_config picks up branch from registry config YAML."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "branch": "seeded",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].branch == "seeded"


def test_load_config_branch_defaults_empty(tmp_path: Path) -> None:
    """load_config defaults branch to empty string when omitted."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].branch == ""


def test_load_config_reads_ssh_command_from_yaml(tmp_path: Path) -> None:
    """load_config picks up ssh_command from registry config YAML."""
    global_dir = tmp_path / "global"
    ssh_cmd = "ssh -i /Users/cpettet/.ssh/github_chasemp.pri -o IdentitiesOnly=yes"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
                "ssh_command": ssh_cmd,
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].ssh_command == ssh_cmd


def test_load_config_ssh_command_defaults_empty(tmp_path: Path) -> None:
    """load_config defaults ssh_command to empty string when omitted."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "remote-reg": {
                "pool_home": "git@github.com:org/repo.git:/data",
                "context": "Remote test.",
            }
        }
    })
    cfg = load_config(global_config_dir=global_dir)
    assert cfg.registries["remote-reg"].ssh_command == ""


# ---------------------------------------------------------------------------
# check_git_state
# ---------------------------------------------------------------------------


def test_check_git_state_not_a_git_repo(tmp_path: Path) -> None:
    """check_git_state returns error when path is not a git repo."""
    result = check_git_state(tmp_path)
    assert not result.valid
    assert any("not a git repository" in e for e in result.errors)


def test_check_git_state_clean_with_remote(tmp_path: Path) -> None:
    """check_git_state returns valid for a clean repo with a remote."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:test/test.git"],
        capture_output=True,
    )
    result = check_git_state(repo)
    assert result.valid, f"Expected valid but got errors: {result.errors}"


def test_check_git_state_no_remote(tmp_path: Path) -> None:
    """check_git_state returns error when no remote is configured."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        capture_output=True,
    )
    result = check_git_state(repo)
    assert not result.valid
    assert any("no remote" in e for e in result.errors)


def test_check_git_state_dirty_working_tree(tmp_path: Path) -> None:
    """check_git_state returns error when working tree has uncommitted changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:test/test.git"],
        capture_output=True,
    )
    (repo / "dirty.txt").write_text("uncommitted")
    result = check_git_state(repo)
    assert not result.valid
    assert any("uncommitted changes" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Reserved names
# ---------------------------------------------------------------------------


def test_reserved_names_includes_all() -> None:
    """'all' is in the reserved names set."""
    assert "all" in RESERVED_NAMES


def test_reserved_names_includes_alph() -> None:
    """'alph' is in the reserved names set."""
    assert "alph" in RESERVED_NAMES


def test_init_registry_writes_remote_fields_to_config(tmp_path: Path) -> None:
    """init_registry writes mode, clone_path, branch, auto_push, auto_pull."""
    global_dir = tmp_path / "global"
    result = init_registry(
        pool_home=Path("git@github.com:org/repo.git:/registry"),
        registry_id="remote-test",
        context="Remote registry.",
        mode="rw",
        clone_path="/tmp/my-clone",
        branch="main",
        auto_push=True,
        auto_pull=True,
        global_config_dir=global_dir,
    )
    assert result.valid
    config = yaml.safe_load((global_dir / "config.yaml").read_text())
    entry = config["registries"]["remote-test"]
    assert entry["pool_home"] == "git@github.com:org/repo.git:/registry"
    assert entry["mode"] == "rw"
    assert entry["clone_path"] == "/tmp/my-clone"
    assert entry["branch"] == "main"
    assert entry["auto_push"] is True
    assert entry["auto_pull"] is True


def test_init_registry_skips_mkdir_for_remote_url(tmp_path: Path) -> None:
    """init_registry does not try to mkdir a git remote URL."""
    global_dir = tmp_path / "global"
    # Use a unique path that definitely does not exist.
    url = "git@github.com:org/unique-test-repo-9999.git:/registry"
    result = init_registry(
        pool_home=Path(url),
        registry_id="remote-test",
        context="Remote registry.",
        mode="ro",
        global_config_dir=global_dir,
    )
    assert result.valid
    config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert config["registries"]["remote-test"]["pool_home"] == url


def test_init_registry_omits_empty_optional_fields(tmp_path: Path) -> None:
    """init_registry does not write empty strings for unset optional fields."""
    global_dir = tmp_path / "global"
    init_registry(
        pool_home=tmp_path / "reg",
        registry_id="local-test",
        context="Local registry.",
        global_config_dir=global_dir,
    )
    config = yaml.safe_load((global_dir / "config.yaml").read_text())
    entry = config["registries"]["local-test"]
    assert "mode" not in entry
    assert "clone_path" not in entry
    assert "branch" not in entry
    assert "auto_push" not in entry
    assert "auto_pull" not in entry
    assert "name" not in entry


def test_init_registry_rejects_reserved_name(tmp_path: Path) -> None:
    """init_registry rejects reserved IDs like 'all'."""
    result = init_registry(
        pool_home=tmp_path / "reg",
        registry_id="all",
        context="Should fail.",
        global_config_dir=tmp_path / "global",
    )
    assert not result.valid
    assert any("reserved" in e for e in result.errors)


def test_init_pool_rejects_reserved_name(tmp_path: Path) -> None:
    """init_pool rejects reserved pool names like 'all'."""
    global_dir = tmp_path / "global"
    # Create a registry first.
    init_registry(
        pool_home=tmp_path / "reg",
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    result = init_pool(
        registry_id="test-reg",
        name="all",
        context="Should fail.",
        cwd=tmp_path / "reg",
        global_config_dir=global_dir,
    )
    assert not result.valid
    assert any("reserved" in e for e in result.errors)


def test_init_pool_preserves_config_key_order(tmp_path: Path) -> None:
    """init_pool does not reorder existing config keys when writing."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    # Write config with a specific key order.
    config_path = global_dir / "config.yaml"
    config_path.write_text(
        "creator: me@example.com\n"
        "default_registry: test-reg\n"
        "registries:\n"
        "  test-reg:\n"
        "    pool_home: " + str(reg_home) + "\n"
        "    context: Test.\n"
    )
    init_pool(
        registry_id="test-reg",
        name="alpha",
        context="First pool.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    text = config_path.read_text()
    lines = text.strip().splitlines()
    # creator should still come before default_registry, and registries last.
    creator_idx = next(i for i, line in enumerate(lines) if line.startswith("creator:"))
    default_idx = next(i for i, line in enumerate(lines) if line.startswith("default_"))
    reg_idx = next(i for i, line in enumerate(lines) if line.startswith("registries:"))
    assert creator_idx < default_idx < reg_idx


def test_init_pool_rejects_ro_remote_registry(tmp_path: Path) -> None:
    """init_pool errors when the target registry is read-only remote."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    config_path = global_dir / "config.yaml"
    config_path.write_text(
        "registries:\n"
        "  remote-ro:\n"
        "    pool_home: git@github.com:org/repo.git:/registry\n"
        "    context: Read-only remote.\n"
        "    mode: ro\n"
    )
    result = init_pool(
        registry_id="remote-ro",
        name="my-pool",
        context="Should fail.",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    assert not result.valid
    assert any("read-only" in e for e in result.errors)


def test_init_pool_rw_remote_requires_clone(tmp_path: Path) -> None:
    """init_pool errors when RW remote registry has no clone on disk."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    config_path = global_dir / "config.yaml"
    config_path.write_text(
        "registries:\n"
        "  remote-rw:\n"
        "    pool_home: git@github.com:org/repo.git:/registry\n"
        "    context: RW remote.\n"
        "    mode: rw\n"
        "    clone_path: " + str(tmp_path / "nonexistent-clone") + "\n"
    )
    result = init_pool(
        registry_id="remote-rw",
        name="my-pool",
        context="Should fail.",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    assert not result.valid
    assert any("clone" in e.lower() for e in result.errors)


def test_init_pool_rw_remote_uses_clone_path(tmp_path: Path) -> None:
    """init_pool creates pool directory inside clone_path + subpath for RW remote."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    clone_dir = tmp_path / "clone"
    (clone_dir / "registry" / ".git").mkdir(parents=True)
    # Make it look like a git repo by putting .git at clone root.
    (clone_dir / ".git").mkdir(exist_ok=True)
    config_path = global_dir / "config.yaml"
    config_path.write_text(
        "registries:\n"
        "  remote-rw:\n"
        "    pool_home: git@github.com:org/repo.git:/registry\n"
        "    context: RW remote.\n"
        "    mode: rw\n"
        "    clone_path: " + str(clone_dir) + "\n"
    )
    result = init_pool(
        registry_id="remote-rw",
        name="my-pool",
        context="A remote pool.",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    assert result.valid
    assert result.pool_path == clone_dir / "registry" / "my-pool"
    assert (result.pool_path / "snapshots").is_dir()
    assert (result.pool_path / "live").is_dir()


def test_init_pool_rejects_duplicate_name_in_config(tmp_path: Path) -> None:
    """init_pool errors when a pool with the same name already exists in config."""
    global_dir = tmp_path / "global"
    init_registry(
        pool_home=tmp_path / "reg",
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    first = init_pool(
        registry_id="test-reg",
        name="vehicles",
        context="First pool.",
        cwd=tmp_path / "reg",
        global_config_dir=global_dir,
    )
    assert first.valid

    second = init_pool(
        registry_id="test-reg",
        name="vehicles",
        context="Duplicate pool.",
        cwd=tmp_path / "reg",
        global_config_dir=global_dir,
    )
    assert not second.valid
    assert any("already exists" in e for e in second.errors)


def test_init_pool_rejects_duplicate_directory_on_disk(tmp_path: Path) -> None:
    """init_pool errors when pool directory exists on disk even if not in config."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    # Create pool directory manually (not via init_pool).
    (reg_home / "sneaky" / "snapshots").mkdir(parents=True)
    (reg_home / "sneaky" / "live").mkdir(parents=True)

    result = init_pool(
        registry_id="test-reg",
        name="sneaky",
        context="Should detect existing dir.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    assert not result.valid
    assert any("already exists" in e for e in result.errors)


# ---------------------------------------------------------------------------
# validate_config_keys — detect unknown config keys
# ---------------------------------------------------------------------------


def test_validate_config_keys_accepts_known_root_keys(tmp_path: Path) -> None:
    """validate_config_keys returns no warnings for valid root-level keys."""
    from alph.core import validate_config_keys

    data = {
        "creator": "a@b.com",
        "auto_commit": True,
        "default_registry": "reg",
        "default_pool": "pool",
        "registries": {},
    }
    warnings = validate_config_keys(data)
    assert warnings == []


def test_validate_config_keys_detects_unknown_root_key(tmp_path: Path) -> None:
    """validate_config_keys flags unrecognized root-level keys."""
    from alph.core import validate_config_keys

    data = {"creator": "a@b.com", "typo_key": "oops"}
    warnings = validate_config_keys(data)
    assert len(warnings) == 1
    assert "typo_key" in warnings[0]


def test_validate_config_keys_detects_unknown_registry_entry_key(tmp_path: Path) -> None:
    """validate_config_keys flags unrecognized keys inside a registry entry."""
    from alph.core import validate_config_keys

    data = {
        "registries": {
            "my-reg": {
                "pool_home": "/some/path",
                "context": "ok",
                "clone_dir": "/wrong/key",  # should be clone_path
            },
        },
    }
    warnings = validate_config_keys(data)
    assert len(warnings) == 1
    assert "clone_dir" in warnings[0]
    assert "my-reg" in warnings[0]


def test_validate_config_keys_accepts_known_registry_entry_keys(tmp_path: Path) -> None:
    """validate_config_keys returns no warnings for valid registry entry keys."""
    from alph.core import validate_config_keys

    data = {
        "registries": {
            "r1": {
                "pool_home": "/path",
                "context": "ctx",
                "name": "n",
                "pools": {},
                "mode": "ro",
                "clone_path": "/c",
                "auto_push": True,
                "auto_pull": False,
                "branch": "main",
            },
        },
    }
    warnings = validate_config_keys(data)
    assert warnings == []


def test_validate_config_keys_detects_legacy_home_key(tmp_path: Path) -> None:
    """validate_config_keys flags legacy 'home' key (should be pool_home)."""
    from alph.core import validate_config_keys

    data = {"registries": {"r": {"home": "/path", "context": "x"}}}
    warnings = validate_config_keys(data)
    assert len(warnings) == 1
    assert "home" in warnings[0]
    assert "pool_home" in warnings[0]


# ---------------------------------------------------------------------------
# validate_config_integrity — referential integrity on merged config
# ---------------------------------------------------------------------------


def test_validate_config_integrity_warns_on_missing_default_registry() -> None:
    """validate_config_integrity warns when default_registry names an unknown registry."""
    from alph.core import AlphConfig, validate_config_integrity

    cfg = AlphConfig(default_registry="ghost", registries={})
    warnings = validate_config_integrity(cfg)
    assert len(warnings) == 1
    assert "default_registry" in warnings[0]
    assert "ghost" in warnings[0]


def test_validate_config_integrity_clean_when_default_registry_exists() -> None:
    """validate_config_integrity returns no warnings when default_registry is valid."""
    from alph.core import AlphConfig, RegistryEntry, validate_config_integrity

    cfg = AlphConfig(
        default_registry="my-reg",
        registries={"my-reg": RegistryEntry(pool_home=Path("/p"), context="c")},
    )
    warnings = validate_config_integrity(cfg)
    assert warnings == []


def test_validate_config_integrity_clean_when_no_defaults_set() -> None:
    """validate_config_integrity returns no warnings when no defaults are configured."""
    from alph.core import AlphConfig, validate_config_integrity

    cfg = AlphConfig()
    warnings = validate_config_integrity(cfg)
    assert warnings == []


def test_validate_config_integrity_clean_when_default_registry_empty() -> None:
    """validate_config_integrity ignores empty default_registry string."""
    from alph.core import AlphConfig, validate_config_integrity

    cfg = AlphConfig(default_registry="", registries={})
    warnings = validate_config_integrity(cfg)
    assert warnings == []


# ---------------------------------------------------------------------------
# .alph.yaml dotfile — pool-local metadata
# ---------------------------------------------------------------------------


def test_init_pool_writes_dotfile_when_register_subdir_pools_false(tmp_path: Path) -> None:
    """init_pool writes .alph.yaml in the pool dir when register_subdir_pools is false."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    # Set register_subdir_pools: false in global config.
    cfg_path = global_dir / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["register_subdir_pools"] = False
    cfg_path.write_text(yaml.dump(data, sort_keys=False))

    result = init_pool(
        registry_id="test-reg",
        name="mypool",
        context="Pool context.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    assert result.valid
    # .alph.yaml should exist in pool dir.
    dotfile = result.pool_path / ".alph.yaml"
    assert dotfile.exists()
    dot_data = yaml.safe_load(dotfile.read_text())
    assert dot_data["context"] == "Pool context."
    assert "creator" in dot_data
    assert "created" in dot_data


def test_init_pool_dotfile_no_config_entry_when_register_subdir_pools_false(tmp_path: Path) -> None:
    """init_pool does NOT write a pool entry to config when register_subdir_pools is false."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    cfg_path = global_dir / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["register_subdir_pools"] = False
    cfg_path.write_text(yaml.dump(data, sort_keys=False))

    init_pool(
        registry_id="test-reg",
        name="mypool",
        context="Pool context.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    # Re-read config — should NOT have a pools entry.
    final_data = yaml.safe_load(cfg_path.read_text())
    reg_entry = final_data["registries"]["test-reg"]
    assert "pools" not in reg_entry or "mypool" not in reg_entry.get("pools", {})


def test_init_pool_writes_config_entry_when_register_subdir_pools_true(tmp_path: Path) -> None:
    """init_pool writes both .alph.yaml and config entry when register_subdir_pools is true."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    cfg_path = global_dir / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["register_subdir_pools"] = True
    cfg_path.write_text(yaml.dump(data, sort_keys=False))

    result = init_pool(
        registry_id="test-reg",
        name="mypool",
        context="Pool context.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    assert result.valid
    # .alph.yaml should exist.
    assert (result.pool_path / ".alph.yaml").exists()
    # Config should also have the pool entry.
    final_data = yaml.safe_load(cfg_path.read_text())
    assert "mypool" in final_data["registries"]["test-reg"].get("pools", {})


def test_init_pool_default_register_subdir_pools_is_false(tmp_path: Path) -> None:
    """register_subdir_pools defaults to false — init_pool writes dotfile, not config entry."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )

    result = init_pool(
        registry_id="test-reg",
        name="mypool",
        context="Pool context.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    assert result.valid
    assert (result.pool_path / ".alph.yaml").exists()
    # Config should NOT have pools entry.
    cfg_path = global_dir / "config.yaml"
    final_data = yaml.safe_load(cfg_path.read_text())
    reg_entry = final_data["registries"]["test-reg"]
    assert "pools" not in reg_entry or "mypool" not in reg_entry.get("pools", {})


def test_list_pools_reads_dotfile_context(tmp_path: Path) -> None:
    """list_pools reads .alph.yaml from discovered pool dirs for context."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    # Manually create a pool dir with .alph.yaml (no config entry).
    pool_dir = reg_home / "manual-pool"
    (pool_dir / "snapshots").mkdir(parents=True)
    (pool_dir / "live").mkdir(parents=True)
    (pool_dir / ".alph.yaml").write_text(yaml.dump({
        "context": "Manually created pool.",
        "creator": "a@b.com",
        "created": "2026-03-11T00:00:00Z",
    }))

    cfg = load_config(global_config_dir=global_dir, cwd=reg_home)
    pools = list_pools("test-reg", cfg=cfg)
    assert pools is not None
    names = {p.name: p for p in pools}
    assert "manual-pool" in names
    assert names["manual-pool"].context == "Manually created pool."
    assert names["manual-pool"].source == "discovered"


def test_list_pools_discovers_pools_in_rw_remote_clone(tmp_path: Path) -> None:
    """list_pools discovers pools on disk for RW remote registries with clone_path."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    (clone_dir / "socialauth" / "snapshots").mkdir(parents=True)
    (clone_dir / "socialauth" / "live").mkdir(parents=True)
    (clone_dir / "seamless" / "snapshots").mkdir(parents=True)
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "spp": {
                "pool_home": "git@github.com:org/repo.git",
                "context": "Test RW remote.",
                "mode": "rw",
                "clone_path": str(clone_dir),
            },
        },
    })
    cfg = load_config(global_config_dir=global_dir)
    result = list_pools("spp", cfg=cfg)
    assert result is not None
    names = {s.name for s in result}
    assert "socialauth" in names
    assert "seamless" in names


def test_list_pools_discovers_pools_in_rw_remote_clone_with_subpath(tmp_path: Path) -> None:
    """list_pools uses subpath within clone_path for remote registries with subpath."""
    global_dir = tmp_path / "global"
    clone_dir = tmp_path / "clone"
    (clone_dir / "registry" / "vehicles" / "snapshots").mkdir(parents=True)
    _write_config(global_dir / "config.yaml", {
        "registries": {
            "demo": {
                "pool_home": "git@github.com:org/repo.git:/registry",
                "context": "Test RW remote with subpath.",
                "mode": "rw",
                "clone_path": str(clone_dir),
            },
        },
    })
    cfg = load_config(global_config_dir=global_dir)
    result = list_pools("demo", cfg=cfg)
    assert result is not None
    names = {s.name for s in result}
    assert "vehicles" in names


def test_init_pool_dotfile_duplicate_detection(tmp_path: Path) -> None:
    """init_pool detects duplicate when pool dir with .alph.yaml already exists."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    # First init should succeed.
    result1 = init_pool(
        registry_id="test-reg",
        name="dup-pool",
        context="First.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    assert result1.valid

    # Second init should fail (directory already exists).
    result2 = init_pool(
        registry_id="test-reg",
        name="dup-pool",
        context="Second.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    assert not result2.valid
    assert any("already exists" in e for e in result2.errors)


def test_init_pool_repo_type_always_writes_config_entry(tmp_path: Path) -> None:
    """init_pool for pool_type='repo' always writes a config entry regardless of register_subdir_pools."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "reg"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    cfg_path = global_dir / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["register_subdir_pools"] = False
    cfg_path.write_text(yaml.dump(data, sort_keys=False))

    result = init_pool(
        registry_id="test-reg",
        name="repo-pool",
        context="Repo pool.",
        pool_type="repo",
        cwd=reg_home,
        global_config_dir=global_dir,
    )
    assert result.valid
    # Repo pools always go in config.
    final_data = yaml.safe_load(cfg_path.read_text())
    assert "repo-pool" in final_data["registries"]["test-reg"].get("pools", {})


# ---------------------------------------------------------------------------
# Comment preservation — init_registry and init_pool must not strip comments
# ---------------------------------------------------------------------------


def test_init_registry_preserves_top_level_comment(tmp_path: Path) -> None:
    """A comment at the top of config.yaml survives registry init."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    config_path = global_dir / "config.yaml"
    config_path.write_text(
        "# Personal alph configuration — do not auto-generate\n"
        "creator: test@example.com\n"
        "registries:\n"
        "  existing:\n"
        "    pool_home: /tmp/existing\n"
        "    context: Existing registry.\n"
    )

    init_registry(
        pool_home=tmp_path / "new-reg",
        registry_id="new-reg",
        context="New registry.",
        global_config_dir=global_dir,
    )

    assert "# Personal alph configuration" in config_path.read_text()


def test_init_registry_preserves_comment_above_existing_registry_entry(tmp_path: Path) -> None:
    """A comment above an existing registry entry survives when a new registry is added."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    config_path = global_dir / "config.yaml"
    config_path.write_text(
        "creator: test@example.com\n"
        "registries:\n"
        "  # my household registry\n"
        "  household:\n"
        "    pool_home: /tmp/household\n"
        "    context: Household context.\n"
    )

    init_registry(
        pool_home=tmp_path / "work",
        registry_id="work",
        context="Work registry.",
        global_config_dir=global_dir,
    )

    result = config_path.read_text()
    assert "# my household registry" in result
    assert "work" in result


def test_init_registry_preserves_inline_comment_on_existing_key(tmp_path: Path) -> None:
    """An inline comment on an existing top-level key survives registry init."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    config_path = global_dir / "config.yaml"
    config_path.write_text(
        "creator: test@example.com  # primary identity\n"
        "registries:\n"
        "  r1:\n"
        "    pool_home: /tmp/r1\n"
        "    context: R1.\n"
    )

    init_registry(
        pool_home=tmp_path / "r2",
        registry_id="r2",
        context="R2.",
        global_config_dir=global_dir,
    )

    assert "# primary identity" in config_path.read_text()


def test_init_pool_preserves_top_level_comment(tmp_path: Path) -> None:
    """A comment at the top of config.yaml survives pool init."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "registry"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    config_path = global_dir / "config.yaml"
    # Inject a comment into the file that init_pool must preserve.
    original = config_path.read_text()
    config_path.write_text("# managed by hand — preserve this\n" + original)

    init_pool(
        registry_id="test-reg",
        name="vehicles",
        context="Cars.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )

    assert "# managed by hand" in config_path.read_text()


def test_init_pool_preserves_comment_above_registry_entry(tmp_path: Path) -> None:
    """A comment above the target registry entry survives pool init."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "registry"
    init_registry(
        pool_home=reg_home,
        registry_id="test-reg",
        context="Test.",
        global_config_dir=global_dir,
    )
    config_path = global_dir / "config.yaml"
    # Insert a comment above the registry entry.
    text = config_path.read_text()
    text = text.replace("  test-reg:\n", "  # scratch registry for testing\n  test-reg:\n")
    config_path.write_text(text)

    init_pool(
        registry_id="test-reg",
        name="vehicles",
        context="Cars.",
        cwd=reg_home,
        global_config_dir=global_dir,
    )

    assert "# scratch registry for testing" in config_path.read_text()


# ---------------------------------------------------------------------------
# content_type field
# ---------------------------------------------------------------------------


def test_valid_content_types_set_is_not_empty() -> None:
    """_VALID_CONTENT_TYPES contains the expected values."""
    assert "text" in _VALID_CONTENT_TYPES
    assert "gdoc" in _VALID_CONTENT_TYPES
    assert "slack" in _VALID_CONTENT_TYPES
    assert "jira" in _VALID_CONTENT_TYPES


def test_node_without_content_type_passes_validation() -> None:
    """A node without content_type is valid — text is the implicit default."""
    result = validate_node(_base_node())
    assert result.valid is True


def test_node_with_text_content_type_passes_validation() -> None:
    """A node with content_type: text passes validation."""
    result = validate_node(_base_node(content_type="text"))
    assert result.valid is True


def test_node_with_unknown_content_type_fails_validation() -> None:
    """A node with an unrecognised content_type fails validation."""
    result = validate_node(_base_node(content_type="cli"))
    assert result.valid is False
    assert any("content_type" in e for e in result.errors)


def test_node_with_gdoc_content_type_and_url_passes_validation() -> None:
    """A gdoc node with meta.url passes validation."""
    node = _base_node(content_type="gdoc", meta={"url": "https://docs.google.com/document/d/abc"})
    result = validate_node(node)
    assert result.valid is True


def test_node_with_gdoc_content_type_missing_url_fails_validation() -> None:
    """A gdoc node without meta.url fails validation."""
    node = _base_node(content_type="gdoc", meta={"title": "Some Doc"})
    result = validate_node(node)
    assert result.valid is False
    assert any("meta.url" in e for e in result.errors)


def test_node_with_jira_content_type_and_required_meta_passes_validation() -> None:
    """A jira node with meta.url and meta.issue_key passes validation."""
    node = _base_node(
        content_type="jira",
        meta={"url": "https://jira.example.com/browse/AUTH-123", "issue_key": "AUTH-123"},
    )
    result = validate_node(node)
    assert result.valid is True


def test_node_with_jira_content_type_missing_issue_key_fails_validation() -> None:
    """A jira node without meta.issue_key fails validation."""
    node = _base_node(
        content_type="jira",
        meta={"url": "https://jira.example.com/browse/AUTH-123"},
    )
    result = validate_node(node)
    assert result.valid is False
    assert any("issue_key" in e for e in result.errors)


def test_node_with_slack_content_type_and_url_passes_validation() -> None:
    """A slack node with meta.url satisfies the anchor requirement."""
    node = _base_node(
        content_type="slack",
        meta={"url": "https://slack.com/archives/C123/p456"},
    )
    result = validate_node(node)
    assert result.valid is True


def test_node_with_slack_content_type_and_channel_ts_passes_validation() -> None:
    """A slack node with meta.channel + meta.thread_ts satisfies the anchor requirement."""
    node = _base_node(
        content_type="slack",
        meta={"channel": "C123", "thread_ts": "1234567890.123456"},
    )
    result = validate_node(node)
    assert result.valid is True


def test_node_with_slack_content_type_missing_anchor_fails_validation() -> None:
    """A slack node with neither url nor channel+thread_ts fails validation."""
    node = _base_node(content_type="slack", meta={"text": "some message"})
    result = validate_node(node)
    assert result.valid is False
    assert any("slack" in e for e in result.errors)


def test_create_node_writes_content_type_to_frontmatter(tmp_path: Path) -> None:
    """create_node writes content_type to frontmatter when provided."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Google Doc for auth design",
        creator="chase@example.com",
        content_type="gdoc",
        meta={"url": "https://docs.google.com/document/d/abc"},
    )
    frontmatter = extract_frontmatter(result.path.read_text())
    assert frontmatter is not None
    assert frontmatter["content_type"] == "gdoc"


def test_create_node_omits_content_type_when_not_provided(tmp_path: Path) -> None:
    """create_node does not write content_type when not given — text is implicit."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Plain text note",
        creator="chase@example.com",
    )
    frontmatter = extract_frontmatter(result.path.read_text())
    assert frontmatter is not None
    assert "content_type" not in frontmatter


def test_list_nodes_includes_content_type_in_summary(tmp_path: Path) -> None:
    """list_nodes returns NodeSummary with content_type populated."""
    pool = _make_pool(tmp_path)
    create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Jira ticket AUTH-456",
        creator="chase@example.com",
        content_type="jira",
        meta={"url": "https://jira.example.com/browse/AUTH-456", "issue_key": "AUTH-456"},
    )
    summaries = list_nodes(pool)
    assert len(summaries) == 1
    assert summaries[0].content_type == "jira"


def test_list_nodes_defaults_content_type_to_text_for_nodes_without_field(tmp_path: Path) -> None:
    """list_nodes returns content_type='text' for nodes that don't have the field."""
    pool = _make_pool(tmp_path)
    create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Plain old note",
        creator="chase@example.com",
    )
    summaries = list_nodes(pool)
    assert summaries[0].content_type == "text"


def test_show_node_includes_content_type_in_detail(tmp_path: Path) -> None:
    """show_node returns NodeDetail with content_type populated."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Figma design file",
        creator="chase@example.com",
        content_type="figma",
        meta={"url": "https://figma.com/file/abc"},
    )
    detail = show_node(pool, result.node_id)
    assert detail is not None
    assert detail.content_type == "figma"


def test_show_node_defaults_content_type_to_text(tmp_path: Path) -> None:
    """show_node returns content_type='text' for nodes without the field."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Text note",
        creator="chase@example.com",
    )
    detail = show_node(pool, result.node_id)
    assert detail is not None
    assert detail.content_type == "text"


# ---------------------------------------------------------------------------
# task content_type
# ---------------------------------------------------------------------------


def test_task_content_type_is_valid() -> None:
    """task is a valid content_type."""
    assert "task" in _VALID_CONTENT_TYPES


def test_node_with_task_content_type_passes_validation() -> None:
    """A node with content_type: task passes validation with no required meta."""
    result = validate_node(_base_node(content_type="task"))
    assert result.valid is True


def test_task_node_with_optional_meta_passes_validation() -> None:
    """A task node with optional meta fields (priority, due) passes validation."""
    node = _base_node(content_type="task", meta={"priority": "high", "due": "2026-04-01"})
    result = validate_node(node)
    assert result.valid is True


def test_create_node_writes_task_content_type(tmp_path: Path) -> None:
    """create_node writes content_type: task to frontmatter."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="snapshot",
        context="Fix login bug",
        creator="chase@example.com",
        content_type="task",
        meta={"priority": "high"},
    )
    frontmatter = extract_frontmatter(result.path.read_text())
    assert frontmatter is not None
    assert frontmatter["content_type"] == "task"
    assert frontmatter["meta"]["priority"] == "high"


# ---------------------------------------------------------------------------
# update_node
# ---------------------------------------------------------------------------


def _create_test_node(pool: Path, **kwargs: object) -> str:
    """Create a test node and return its ID."""
    defaults: dict[str, object] = {
        "pool_path": pool,
        "source": "cli",
        "node_type": "snapshot",
        "context": "Test node",
        "creator": "chase@example.com",
    }
    defaults.update(kwargs)
    result = create_node(**defaults)  # type: ignore[arg-type]
    return result.node_id


def test_update_node_changes_status(tmp_path: Path) -> None:
    """update_node changes a node's status from active to archived."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool)
    result = update_node(pool_path=pool, node_id=node_id, status="archived")
    assert result.node_id == node_id
    detail = show_node(pool, node_id)
    assert detail is not None
    assert detail.node_type == "snapshot"
    fm = extract_frontmatter(result.path.read_text())
    assert fm is not None
    assert fm["status"] == "archived"


def test_update_node_tags_add_merges_without_duplicates(tmp_path: Path) -> None:
    """update_node tags_add merges new tags without duplicating existing ones."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, tags=["existing"])
    update_node(pool_path=pool, node_id=node_id, tags_add=["new", "existing"])
    detail = show_node(pool, node_id)
    assert detail is not None
    assert set(detail.tags) == {"existing", "new"}


def test_update_node_tags_remove(tmp_path: Path) -> None:
    """update_node tags_remove removes specified tags."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, tags=["keep", "remove-me"])
    update_node(pool_path=pool, node_id=node_id, tags_remove=["remove-me"])
    detail = show_node(pool, node_id)
    assert detail is not None
    assert detail.tags == ["keep"]


def test_update_node_tags_full_replacement(tmp_path: Path) -> None:
    """update_node tags replaces the entire tags list."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, tags=["old"])
    update_node(pool_path=pool, node_id=node_id, tags=["new1", "new2"])
    detail = show_node(pool, node_id)
    assert detail is not None
    assert set(detail.tags) == {"new1", "new2"}


def test_update_node_tags_and_tags_add_mutually_exclusive(tmp_path: Path) -> None:
    """update_node errors when both tags and tags_add are provided."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool)
    result = update_node(pool_path=pool, node_id=node_id, tags=["a"], tags_add=["b"])
    assert not result.valid
    assert any("mutually exclusive" in e for e in result.errors)


def test_update_node_meta_merges(tmp_path: Path) -> None:
    """update_node meta merges into existing meta dict."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, meta={"existing_key": "val"})
    update_node(pool_path=pool, node_id=node_id, meta={"new_key": "new_val"})
    detail = show_node(pool, node_id)
    assert detail is not None
    assert detail.meta["existing_key"] == "val"
    assert detail.meta["new_key"] == "new_val"


def test_update_node_content_replaces_body(tmp_path: Path) -> None:
    """update_node content replaces the body text."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, content="Original body")
    update_node(pool_path=pool, node_id=node_id, content="New body text")
    detail = show_node(pool, node_id)
    assert detail is not None
    assert detail.body == "New body text"


def test_update_node_context_replaces_frontmatter_field(tmp_path: Path) -> None:
    """update_node context replaces the context frontmatter field."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, context="Original context")
    update_node(pool_path=pool, node_id=node_id, context="Updated context")
    detail = show_node(pool, node_id)
    assert detail is not None
    assert detail.context == "Updated context"


def test_update_node_noop_when_values_unchanged(tmp_path: Path) -> None:
    """update_node returns no-op when the update produces identical content."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, status="active")
    result = update_node(pool_path=pool, node_id=node_id, status="active")
    assert result.noop is True


def test_update_node_not_found(tmp_path: Path) -> None:
    """update_node errors when the node ID does not exist."""
    pool = _make_pool(tmp_path)
    result = update_node(pool_path=pool, node_id="nonexistent1")
    assert not result.valid
    assert any("not found" in e for e in result.errors)


def test_update_node_invalid_status(tmp_path: Path) -> None:
    """update_node errors when given an invalid status value."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool)
    result = update_node(pool_path=pool, node_id=node_id, status="invalid")
    assert not result.valid
    assert any("status" in e for e in result.errors)


def test_update_node_content_type(tmp_path: Path) -> None:
    """update_node can change the content_type field."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool)
    update_node(pool_path=pool, node_id=node_id, content_type="task")
    detail = show_node(pool, node_id)
    assert detail is not None
    assert detail.content_type == "task"


def test_update_node_related_add(tmp_path: Path) -> None:
    """update_node related_add appends to existing related_to list."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, related_to=["aaa111bbb222"])
    update_node(pool_path=pool, node_id=node_id, related_add=["ccc333ddd444"])
    detail = show_node(pool, node_id)
    assert detail is not None
    assert set(detail.related_to) == {"aaa111bbb222", "ccc333ddd444"}


def test_update_node_related_to_full_replacement(tmp_path: Path) -> None:
    """update_node related_to replaces the entire related_to list."""
    pool = _make_pool(tmp_path)
    node_id = _create_test_node(pool, related_to=["old1"])
    update_node(pool_path=pool, node_id=node_id, related_to=["new1", "new2"])
    detail = show_node(pool, node_id)
    assert detail is not None
    assert set(detail.related_to) == {"new1", "new2"}


# ---------------------------------------------------------------------------
# slack validation relaxation — channel-only is valid
# ---------------------------------------------------------------------------


def test_node_with_slack_content_type_and_channel_only_passes_validation() -> None:
    """A slack node with only meta.channel (no thread_ts) is valid — it points to the whole channel."""
    node = _base_node(content_type="slack", meta={"channel": "C123"})
    result = validate_node(node)
    assert result.valid is True


# ---------------------------------------------------------------------------
# HydrationConfig data types
# ---------------------------------------------------------------------------


def test_hydration_type_config_construction() -> None:
    """HydrationTypeConfig can be constructed with all fields."""
    cfg = HydrationTypeConfig(
        provider="google-docs-mcp",
        base_url="https://docs.google.com",
        instructions="Use the Google Docs MCP server.",
    )
    assert cfg.provider == "google-docs-mcp"
    assert cfg.base_url == "https://docs.google.com"
    assert cfg.instructions == "Use the Google Docs MCP server."


def test_hydration_type_config_defaults() -> None:
    """HydrationTypeConfig defaults all fields to empty strings."""
    cfg = HydrationTypeConfig()
    assert cfg.provider == ""
    assert cfg.base_url == ""
    assert cfg.instructions == ""


def test_hydration_type_config_is_immutable() -> None:
    """HydrationTypeConfig is frozen — attributes cannot be reassigned."""
    cfg = HydrationTypeConfig(provider="test")
    try:
        cfg.provider = "changed"  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except AttributeError:
        pass


def test_hydration_config_construction() -> None:
    """HydrationConfig can be constructed with a types dict."""
    cfg = HydrationConfig(types={
        "gdoc": HydrationTypeConfig(provider="google-docs-mcp"),
    })
    assert "gdoc" in cfg.types
    assert cfg.types["gdoc"].provider == "google-docs-mcp"


def test_hydration_config_declared_types() -> None:
    """HydrationConfig.declared_types returns frozenset of type keys."""
    cfg = HydrationConfig(types={
        "gdoc": HydrationTypeConfig(),
        "jira": HydrationTypeConfig(),
    })
    assert cfg.declared_types == frozenset({"gdoc", "jira"})


def test_hydration_config_empty() -> None:
    """HydrationConfig with no types has empty declared_types."""
    cfg = HydrationConfig()
    assert cfg.declared_types == frozenset()
    assert cfg.types == {}


def test_hydration_config_is_immutable() -> None:
    """HydrationConfig is frozen — attributes cannot be reassigned."""
    cfg = HydrationConfig()
    try:
        cfg.types = {}  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# load_hydration_config
# ---------------------------------------------------------------------------


def test_load_hydration_config_missing_file(tmp_path: Path) -> None:
    """load_hydration_config returns empty HydrationConfig when file is missing."""
    cfg = load_hydration_config(tmp_path)
    assert cfg.types == {}
    assert cfg.declared_types == frozenset()


def test_load_hydration_config_valid_file(tmp_path: Path) -> None:
    """load_hydration_config parses all fields from a valid hydration.yaml."""
    hydration_yaml = tmp_path / "hydration.yaml"
    hydration_yaml.write_text(
        "types:\n"
        "  gdoc:\n"
        "    provider: google-docs-mcp\n"
        "    base_url: https://docs.google.com\n"
        "    instructions: Use the Google Docs MCP server.\n"
        "  jira:\n"
        "    provider: atlassian-mcp\n"
        "    instructions: Use Jira.\n"
    )
    cfg = load_hydration_config(tmp_path)
    assert cfg.declared_types == frozenset({"gdoc", "jira"})
    assert cfg.types["gdoc"].provider == "google-docs-mcp"
    assert cfg.types["gdoc"].base_url == "https://docs.google.com"
    assert cfg.types["gdoc"].instructions == "Use the Google Docs MCP server."
    assert cfg.types["jira"].provider == "atlassian-mcp"
    assert cfg.types["jira"].base_url == ""


def test_load_hydration_config_no_types_key(tmp_path: Path) -> None:
    """load_hydration_config returns empty config when file has no types key."""
    hydration_yaml = tmp_path / "hydration.yaml"
    hydration_yaml.write_text("other_key: value\n")
    cfg = load_hydration_config(tmp_path)
    assert cfg.types == {}


def test_load_hydration_config_extra_fields_ignored(tmp_path: Path) -> None:
    """load_hydration_config ignores extra fields in type entries for forward compat."""
    hydration_yaml = tmp_path / "hydration.yaml"
    hydration_yaml.write_text(
        "types:\n"
        "  gdoc:\n"
        "    provider: google-docs-mcp\n"
        "    future_field: some_value\n"
        "    another_field: 42\n"
    )
    cfg = load_hydration_config(tmp_path)
    assert cfg.types["gdoc"].provider == "google-docs-mcp"


# ---------------------------------------------------------------------------
# find_registry_for_pool
# ---------------------------------------------------------------------------


def test_find_registry_for_pool_local_match(tmp_path: Path) -> None:
    """find_registry_for_pool returns the registry whose pool_home contains the pool."""
    reg_home = tmp_path / "registries" / "myrepo"
    pool_path = reg_home / "mypool"
    pool_path.mkdir(parents=True)
    cfg = AlphConfig(registries={
        "myreg": RegistryEntry(pool_home=str(reg_home)),
    })
    result = find_registry_for_pool(pool_path, cfg)
    assert result is not None
    reg_id, entry = result
    assert reg_id == "myreg"
    assert entry.pool_home == str(reg_home)


def test_find_registry_for_pool_no_match(tmp_path: Path) -> None:
    """find_registry_for_pool returns None when no registry matches."""
    cfg = AlphConfig(registries={
        "other": RegistryEntry(pool_home="/some/other/path"),
    })
    result = find_registry_for_pool(tmp_path / "unrelated", cfg)
    assert result is None


def test_find_registry_for_pool_most_specific_wins(tmp_path: Path) -> None:
    """find_registry_for_pool returns the most specific (longest prefix) match."""
    parent = tmp_path / "repos"
    parent.mkdir()
    child = parent / "nested"
    child.mkdir()
    pool_path = child / "mypool"
    pool_path.mkdir()
    cfg = AlphConfig(registries={
        "broad": RegistryEntry(pool_home=str(parent)),
        "specific": RegistryEntry(pool_home=str(child)),
    })
    result = find_registry_for_pool(pool_path, cfg)
    assert result is not None
    reg_id, _ = result
    assert reg_id == "specific"


def test_find_registry_for_pool_clone_path_match(tmp_path: Path) -> None:
    """find_registry_for_pool matches remote RW registries via clone_path."""
    clone_dir = tmp_path / "clones" / "myrepo"
    pool_path = clone_dir / "mypool"
    pool_path.mkdir(parents=True)
    cfg = AlphConfig(registries={
        "remote_rw": RegistryEntry(
            pool_home="git@github.com:org/repo.git:/",
            mode="rw",
            clone_path=str(clone_dir),
        ),
    })
    result = find_registry_for_pool(pool_path, cfg)
    assert result is not None
    reg_id, _ = result
    assert reg_id == "remote_rw"


# ---------------------------------------------------------------------------
# validate_node with registry_types
# ---------------------------------------------------------------------------


def test_validate_node_builtin_types_still_validated_strictly() -> None:
    """Built-in types are still validated strictly even when registry_types is provided."""
    node = _base_node(content_type="jira", meta={"url": "https://jira.example.com/PROJ-1"})
    result = validate_node(node, registry_types=frozenset({"custom_type"}))
    assert result.valid is False
    assert any("issue_key" in e for e in result.errors)


def test_validate_node_unknown_type_without_registry_types_fails() -> None:
    """An unknown content_type fails validation when no registry_types is provided."""
    node = _base_node(content_type="custom_widget")
    result = validate_node(node)
    assert result.valid is False
    assert any("custom_widget" in e for e in result.errors)


def test_validate_node_unknown_type_in_registry_types_passes() -> None:
    """An unknown content_type passes when it's in registry_types."""
    node = _base_node(content_type="custom_widget")
    result = validate_node(node, registry_types=frozenset({"custom_widget"}))
    assert result.valid is True


def test_validate_node_builtin_type_uses_builtin_rules_not_registry() -> None:
    """A type in both built-in and registry_types uses built-in validation rules."""
    node = _base_node(content_type="gdoc")  # gdoc requires meta.url
    result = validate_node(node, registry_types=frozenset({"gdoc"}))
    assert result.valid is False
    assert any("url" in e for e in result.errors)


def test_validate_node_no_registry_types_backwards_compat() -> None:
    """validate_node without registry_types works as before."""
    node = _base_node()
    result = validate_node(node)
    assert result.valid is True


# ---------------------------------------------------------------------------
# NodeDetail hydration_instructions and show_node with hydration
# ---------------------------------------------------------------------------


def test_node_detail_has_hydration_instructions_field() -> None:
    """NodeDetail includes hydration_instructions defaulting to empty string."""
    detail = NodeDetail(
        node_id="abc123def456",
        context="test",
        node_type="snapshot",
        timestamp="2026-03-16T00:00:00Z",
        source="cli",
        creator="test@example.com",
        body="",
    )
    assert detail.hydration_instructions == ""


def test_show_node_without_hydration_returns_empty_instructions(tmp_path: Path) -> None:
    """show_node without hydration config returns empty hydration_instructions."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool, source="cli", node_type="live",
        context="A Google Doc", creator="chase@example.com",
        content_type="gdoc", meta={"url": "https://docs.google.com/d/abc"},
    )
    detail = show_node(pool, result.node_id)
    assert detail is not None
    assert detail.hydration_instructions == ""


def test_show_node_with_matching_hydration_returns_instructions(tmp_path: Path) -> None:
    """show_node with matching hydration config populates hydration_instructions."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool, source="cli", node_type="live",
        context="A Google Doc", creator="chase@example.com",
        content_type="gdoc", meta={"url": "https://docs.google.com/d/abc"},
    )
    hydration = HydrationConfig(types={
        "gdoc": HydrationTypeConfig(
            provider="google-docs-mcp",
            instructions="Use the Google Docs MCP server.",
        ),
    })
    detail = show_node(pool, result.node_id, hydration=hydration)
    assert detail is not None
    assert detail.hydration_instructions == "Use the Google Docs MCP server."


def test_show_node_with_non_matching_hydration_returns_empty(tmp_path: Path) -> None:
    """show_node with hydration config that doesn't match content_type returns empty."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool, source="cli", node_type="live",
        context="A Slack channel", creator="chase@example.com",
        content_type="slack", meta={"channel": "C123"},
    )
    hydration = HydrationConfig(types={
        "gdoc": HydrationTypeConfig(instructions="Google docs stuff"),
    })
    detail = show_node(pool, result.node_id, hydration=hydration)
    assert detail is not None
    assert detail.hydration_instructions == ""
