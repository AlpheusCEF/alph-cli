"""Behavior tests for alph core logic."""

import subprocess
from pathlib import Path

import yaml

from alph.core import (
    AlphConfig,
    check_idempotency,
    collect_registries,
    create_node,
    default_global_config_text,
    extract_frontmatter,
    find_registry_config,
    generate_id,
    init_pool,
    init_registry,
    list_config_paths,
    list_nodes,
    load_config,
    load_state,
    resolve_default_pool,
    show_node,
    update_state,
    validate_node,
)


def _make_pool(base: Path) -> Path:
    """Create a minimal pool directory structure."""
    pool = base / "my-pool"
    (pool / "snapshots").mkdir(parents=True)
    (pool / "pointers").mkdir(parents=True)
    return pool


def _write_node(directory: Path, node_id: str, creator: str, timestamp: str) -> None:
    """Write a minimal node file into a pool subdirectory."""
    content = (
        f"---\nschema_version: '1'\nid: {node_id}\ntimestamp: '{timestamp}'\n"
        f"source: cli\nnode_type: fixed\ncontext: test node\ncreator: {creator}\n---\n"
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
    _write_node(pool / "pointers", "a1b2c3d4e5f6", "chase@example.com", "2026-03-05T10:00:00Z")
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
    create_node(pool_path=pool, source="cli", node_type="fixed",
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
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Brake pads at 40%", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    summaries = list_nodes(pool)
    s = summaries[0]
    assert s.node_id
    assert s.context == "Brake pads at 40%"
    assert s.node_type == "fixed"
    assert s.timestamp


def test_show_node_returns_full_content_by_id(tmp_path: Path) -> None:
    """show_node returns the full NodeDetail for a matching node ID."""
    pool = _make_pool(tmp_path)
    result = create_node(pool_path=pool, source="cli", node_type="fixed",
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


def test_init_registry_creates_config_with_registry_declaration(tmp_path: Path) -> None:
    """init_registry creates a config.yaml containing the registry declaration."""
    result = init_registry(
        home=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Personal context pools",
    )
    assert result.config_path.exists()
    config = yaml.safe_load(result.config_path.read_text())
    assert config["registries"]["reg-01"]["context"] == "Personal context pools"


def test_init_registry_validates_its_own_output(tmp_path: Path) -> None:
    """init_registry result passes registry validation."""
    result = init_registry(
        home=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Personal context pools",
    )
    assert result.valid is True


def test_init_registry_sets_default_when_no_existing_default(tmp_path: Path) -> None:
    """init_registry writes default_registry and registry path into the GLOBAL config."""
    global_dir = tmp_path / "global"
    result = init_registry(
        home=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Test registry",
        global_config_dir=global_dir,
    )
    assert result.set_as_default is True
    # The global config should now have the registry path and the default.
    global_config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert global_config["default_registry"] == "reg-01"
    assert "reg-01" in global_config["registries"]
    # The registry's own config should NOT have default_registry.
    local_config = yaml.safe_load(result.config_path.read_text())
    assert "default_registry" not in local_config


def test_init_registry_does_not_override_existing_default(tmp_path: Path) -> None:
    """init_registry does not change default_registry when one already exists."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {"default_registry": "existing"})
    result = init_registry(
        home=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Test registry",
        global_config_dir=global_dir,
    )
    assert result.set_as_default is False
    global_config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert global_config["default_registry"] == "existing"
    # Registry path still gets registered even when not the default.
    assert "reg-01" in global_config["registries"]


def test_find_registry_config_finds_by_id_in_cfg(tmp_path: Path) -> None:
    """find_registry_config returns (actual_id, home_path) when registry ID is in cfg."""
    cfg = AlphConfig(registries={"reg-01": str(tmp_path / "home")})
    result = find_registry_config("reg-01", cfg=cfg)
    assert result is not None
    actual_id, home = result
    assert actual_id == "reg-01"
    assert home == tmp_path / "home"


def test_find_registry_config_finds_by_name_via_home_config(tmp_path: Path) -> None:
    """find_registry_config matches on registry name by reading the home config."""
    reg_home = tmp_path / "home"
    init_registry(home=reg_home, registry_id="reg-01", context="Test", name="My Registry")
    cfg = AlphConfig(registries={"reg-01": str(reg_home)})
    result = find_registry_config("My Registry", cfg=cfg)
    assert result is not None
    actual_id, home = result
    assert actual_id == "reg-01"
    assert home == reg_home


def test_find_registry_config_returns_none_when_not_in_cfg(tmp_path: Path) -> None:
    """find_registry_config returns None when the ID is not in cfg.registries."""
    cfg = AlphConfig(registries={"other-reg": str(tmp_path)})
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
    init_registry(home=reg_home, registry_id="reg-01", context="My context", name="My Registry")
    cfg = AlphConfig(registries={"reg-01": str(reg_home)})
    summaries = collect_registries(cfg=cfg)
    assert len(summaries) == 1
    assert summaries[0].registry_id == "reg-01"
    assert summaries[0].name == "My Registry"
    assert summaries[0].context == "My context"


def test_collect_registries_returns_empty_when_no_registries(tmp_path: Path) -> None:
    """collect_registries returns an empty list when cfg has no registries."""
    cfg = AlphConfig()
    assert collect_registries(cfg=cfg) == []


def test_collect_registries_config_path_points_to_home_config(tmp_path: Path) -> None:
    """collect_registries reports the config_path as the registry home config.yaml."""
    reg_home = tmp_path / "home"
    init_registry(home=reg_home, registry_id="reg-01", context="Test")
    cfg = AlphConfig(registries={"reg-01": str(reg_home)})
    summaries = collect_registries(cfg=cfg)
    assert summaries[0].config_path == reg_home / "config.yaml"


def test_init_pool_creates_required_directories(tmp_path: Path) -> None:
    """init_pool creates snapshots/, pointers/, and .alph/ inside the pool."""
    init_registry(home=tmp_path, registry_id="reg-01", context="Test registry",
                  global_config_dir=tmp_path / "no-global")
    result = init_pool(
        registry_id="reg-01",
        name="highlander",
        context="Maintenance for the Highlander",
        cwd=tmp_path,
        global_config_dir=tmp_path / "no-global",
    )
    assert (result.pool_path / "snapshots").is_dir()
    assert (result.pool_path / "pointers").is_dir()
    assert (result.pool_path / ".alph").is_dir()


def test_init_pool_registers_pool_in_registry_config(tmp_path: Path) -> None:
    """init_pool adds the pool entry to the config file that owns the registry."""
    init_registry(home=tmp_path, registry_id="reg-01", context="Test registry",
                  global_config_dir=tmp_path / "no-global")
    result = init_pool(
        registry_id="reg-01",
        name="highlander",
        context="Maintenance for the Highlander",
        cwd=tmp_path,
        global_config_dir=tmp_path / "no-global",
    )
    config = yaml.safe_load(result.config_path.read_text())
    assert "highlander" in config["pools"]
    assert config["pools"]["highlander"]["context"] == "Maintenance for the Highlander"


def test_init_pool_validates_its_own_output(tmp_path: Path) -> None:
    """init_pool result passes registry validation."""
    init_registry(home=tmp_path, registry_id="reg-01", context="Test registry",
                  global_config_dir=tmp_path / "no-global")
    result = init_pool(
        registry_id="reg-01",
        name="highlander",
        context="Maintenance for the Highlander",
        cwd=tmp_path,
        global_config_dir=tmp_path / "no-global",
    )
    assert result.valid is True


def test_init_pool_errors_when_registry_not_found(tmp_path: Path) -> None:
    """init_pool returns an invalid result when the registry ID is not found."""
    result = init_pool(
        registry_id="nonexistent",
        name="vehicles",
        context="Vehicles",
        cwd=tmp_path,
        global_config_dir=tmp_path / "no-global",
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
        global_config_dir=tmp_path / "no-global",
        bootstrap=True,
        registry_context="Bootstrapped registry",
    )
    assert result.valid is True
    assert (result.pool_path / "snapshots").is_dir()


def test_init_pool_writes_default_pool_to_global_config(tmp_path: Path) -> None:
    """init_pool sets default_pool in the global config when this is the default registry."""
    global_dir = tmp_path / "global"
    init_registry(home=tmp_path, registry_id="reg-01", context="Test",
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
        "registries": {"reg-01": str(tmp_path)},
    })
    init_registry(home=tmp_path, registry_id="reg-01", context="Test",
                  global_config_dir=global_dir)
    init_pool(
        registry_id="reg-01",
        name="new-pool",
        context="New pool",
        cwd=tmp_path,
        global_config_dir=global_dir,
    )
    global_config = yaml.safe_load((global_dir / "config.yaml").read_text())
    assert global_config["default_pool"] == "existing-pool"


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
    assert config.registries["household"] == "/local/household"


def test_load_config_picks_up_registry_from_home_config_in_cwd_walk(tmp_path: Path) -> None:
    """load_config walking cwd finds a registry home config and maps its ID to the home path."""
    global_dir = tmp_path / "global"
    reg_home = tmp_path / "my-registry"
    # init_registry writes home config with dict format; no global_config_dir → not in global config
    init_registry(home=reg_home, registry_id="reg-01", context="Test")
    # Load from a cwd that is the registry home — walk-up will find it
    config = load_config(global_config_dir=global_dir, cwd=reg_home)
    assert "reg-01" in config.registries
    assert config.registries["reg-01"] == str(reg_home)


def test_resolve_default_pool_returns_path_when_configured(tmp_path: Path) -> None:
    """resolve_default_pool returns registry_path/pool_name when both are configured."""
    registry_path = tmp_path / "registry"
    config = AlphConfig(
        default_registry="household",
        default_pool="vehicles",
        registries={"household": str(registry_path)},
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
        registries={"household": "/registries/household"},
    )
    assert resolve_default_pool(config) is None


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
        node_type="fixed",
        context="Auto-committed node",
        creator="test@example.com",
        auto_commit=True,
    )

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    )
    assert f"alph: add fixed node {result.node_id}" in log.stdout


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
        node_type="fixed",
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
        node_type="fixed",
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
    assert result.path.parent == pool / "pointers"


def test_create_node_frontmatter_is_valid(tmp_path: Path) -> None:
    """The file created by create_node has valid frontmatter that passes validate_node."""
    pool = _make_pool(tmp_path)
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type="fixed",
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
        node_type="fixed",
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
        node_type="fixed",
        context="Oil change at Valvoline",
        creator="chase@example.com",
        timestamp="2026-03-05T10:00:00Z",
    )
    create_node(**kwargs)  # type: ignore[arg-type]
    result = create_node(**kwargs)  # type: ignore[arg-type]
    assert result.duplicate is True
    assert result.existing_creator == "chase@example.com"


def test_generate_id_returns_12_char_hex() -> None:
    """generate_id returns a 12-character lowercase hex string."""
    node_id = generate_id(
        timestamp="2026-03-05T10:00:00Z",
        source="cli",
        context="Oil change at Valvoline",
    )
    assert len(node_id) == 12
    assert all(c in "0123456789abcdef" for c in node_id)


def test_generate_id_is_deterministic() -> None:
    """generate_id returns the same ID for the same inputs."""
    kwargs = {"timestamp": "2026-03-05T10:00:00Z", "source": "cli", "context": "Oil change"}
    assert generate_id(**kwargs) == generate_id(**kwargs)


def test_generate_id_differs_for_different_inputs() -> None:
    """generate_id returns different IDs for different context values."""
    id1 = generate_id(timestamp="2026-03-05T10:00:00Z", source="cli", context="Oil change")
    id2 = generate_id(timestamp="2026-03-05T10:00:00Z", source="cli", context="Brake check")
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
        "node_type": "fixed",
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
        "node_type": "snapshot",
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
        "node_type": "fixed",
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
        "node_type": "fixed",
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
        node_type="fixed",
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
        node_type="fixed",
        context="Normal node",
        creator="chase@example.com",
    )
    frontmatter = extract_frontmatter(result.path.read_text())
    assert frontmatter is not None
    assert "status" not in frontmatter


def test_list_nodes_excludes_archived_by_default(tmp_path: Path) -> None:
    """list_nodes omits archived nodes from default results."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Active node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Archived node", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="archived")
    summaries = list_nodes(pool)
    contexts = {s.context for s in summaries}
    assert "Active node" in contexts
    assert "Archived node" not in contexts


def test_list_nodes_excludes_suppressed_by_default(tmp_path: Path) -> None:
    """list_nodes omits suppressed nodes from default results."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Active node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Suppressed node", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="suppressed")
    summaries = list_nodes(pool)
    contexts = {s.context for s in summaries}
    assert "Active node" in contexts
    assert "Suppressed node" not in contexts


def test_list_nodes_includes_archived_when_requested(tmp_path: Path) -> None:
    """list_nodes includes archived nodes when include_statuses contains archived."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Active node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Archived node", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="archived")
    summaries = list_nodes(pool, include_statuses={"active", "archived"})
    contexts = {s.context for s in summaries}
    assert "Active node" in contexts
    assert "Archived node" in contexts


def test_list_nodes_includes_all_statuses_when_all_requested(tmp_path: Path) -> None:
    """list_nodes returns every node when include_statuses contains all three values."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Active node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z")
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Archived node", creator="chase@example.com",
                timestamp="2026-03-05T11:00:00Z", status="archived")
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Suppressed node", creator="chase@example.com",
                timestamp="2026-03-05T12:00:00Z", status="suppressed")
    summaries = list_nodes(pool, include_statuses={"active", "archived", "suppressed"})
    assert len(summaries) == 3


def test_node_summary_exposes_status(tmp_path: Path) -> None:
    """NodeSummary from list_nodes includes the status field."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="fixed",
                context="Archived node", creator="chase@example.com",
                timestamp="2026-03-05T10:00:00Z", status="archived")
    summaries = list_nodes(pool, include_statuses={"active", "archived"})
    assert summaries[0].status == "archived"


def test_node_summary_status_defaults_to_active_when_absent(tmp_path: Path) -> None:
    """NodeSummary reports status as active when frontmatter has no status field."""
    pool = _make_pool(tmp_path)
    create_node(pool_path=pool, source="cli", node_type="fixed",
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
