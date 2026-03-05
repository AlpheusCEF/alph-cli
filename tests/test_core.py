"""Behavior tests for alph core logic."""

from pathlib import Path

import yaml

from alph.core import (
    AlphConfig,
    check_idempotency,
    create_node,
    extract_frontmatter,
    generate_id,
    init_pool,
    init_registry,
    list_nodes,
    load_config,
    load_state,
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
        path=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Personal context pools",
    )
    assert result.config_path.exists()
    config = yaml.safe_load(result.config_path.read_text())
    assert config["registries"]["reg-01"]["context"] == "Personal context pools"


def test_init_registry_validates_its_own_output(tmp_path: Path) -> None:
    """init_registry result passes registry validation."""
    result = init_registry(
        path=tmp_path / "my-registry",
        registry_id="reg-01",
        context="Personal context pools",
    )
    assert result.valid is True


def test_init_pool_creates_required_directories(tmp_path: Path) -> None:
    """init_pool creates snapshots/, pointers/, and .alph/ inside the pool."""
    registry_path = tmp_path / "registry"
    init_registry(path=registry_path, registry_id="reg-01", context="Test registry")
    result = init_pool(
        registry_path=registry_path,
        name="highlander",
        context="Maintenance for the Highlander",
    )
    assert (result.pool_path / "snapshots").is_dir()
    assert (result.pool_path / "pointers").is_dir()
    assert (result.pool_path / ".alph").is_dir()


def test_init_pool_registers_pool_in_registry_config(tmp_path: Path) -> None:
    """init_pool adds the pool entry to the registry config."""
    registry_path = tmp_path / "registry"
    init_registry(path=registry_path, registry_id="reg-01", context="Test registry")
    init_pool(
        registry_path=registry_path,
        name="highlander",
        context="Maintenance for the Highlander",
    )
    config = yaml.safe_load((registry_path / "config.yaml").read_text())
    assert "highlander" in config["pools"]
    assert config["pools"]["highlander"]["context"] == "Maintenance for the Highlander"


def test_init_pool_validates_its_own_output(tmp_path: Path) -> None:
    """init_pool result passes registry validation."""
    registry_path = tmp_path / "registry"
    init_registry(path=registry_path, registry_id="reg-01", context="Test registry")
    result = init_pool(
        registry_path=registry_path,
        name="highlander",
        context="Maintenance for the Highlander",
    )
    assert result.valid is True


def test_load_config_returns_defaults_when_no_files_exist(tmp_path: Path) -> None:
    """load_config returns an AlphConfig with defaults when no config files are present."""
    config = load_config(global_config_dir=tmp_path / "global", pool_path=tmp_path / "pool")
    assert isinstance(config, AlphConfig)
    assert config.auto_commit is False


def test_load_config_reads_global_creator(tmp_path: Path) -> None:
    """load_config picks up creator email from the global config file."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {"creator": "chase@example.com"})
    config = load_config(global_config_dir=global_dir, pool_path=tmp_path / "pool")
    assert config.creator == "chase@example.com"


def test_load_config_pool_overrides_global(tmp_path: Path) -> None:
    """Pool-level config overrides global config for the same key."""
    global_dir = tmp_path / "global"
    pool_dir = tmp_path / "pool"
    _write_config(global_dir / "config.yaml", {"creator": "global@example.com", "auto_commit": False})
    _write_config(pool_dir / ".alph" / "config.yaml", {"creator": "pool@example.com"})
    config = load_config(global_config_dir=global_dir, pool_path=pool_dir)
    assert config.creator == "pool@example.com"
    assert config.auto_commit is False


def test_load_config_cli_overrides_override_everything(tmp_path: Path) -> None:
    """CLI overrides take precedence over both global and pool config."""
    global_dir = tmp_path / "global"
    _write_config(global_dir / "config.yaml", {"creator": "global@example.com"})
    config = load_config(
        global_config_dir=global_dir,
        pool_path=tmp_path / "pool",
        overrides={"creator": "cli@example.com"},
    )
    assert config.creator == "cli@example.com"


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
