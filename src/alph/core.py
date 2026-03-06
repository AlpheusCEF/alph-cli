"""AlpheusCEF core logic. No framework dependencies. All business logic lives here."""

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """Result of a validation check."""

    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AlphConfig:
    """Merged configuration from global, pool, and CLI override layers.

    registries maps registry ID -> path string. Accumulates across the config
    cascade — later files add to (or override individual entries in) the map
    rather than replacing it wholesale.
    """

    creator: str = ""
    auto_commit: bool = False
    default_registry: str = ""
    default_pool: str = ""
    registries: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExistingNode:
    """Metadata returned when a duplicate node is detected."""

    creator: str
    timestamp: str


@dataclass(frozen=True)
class NodeResult:
    """Result of a create_node call."""

    node_id: str
    path: Path
    duplicate: bool = False
    existing_creator: str = ""


@dataclass(frozen=True)
class TimelineState:
    """Persistent state tracking for a pool."""

    last_loaded: str | None = None
    node_verified: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeSummary:
    """Lightweight summary of a node for list display."""

    node_id: str
    context: str
    node_type: str
    timestamp: str
    source: str
    status: str = "active"


@dataclass(frozen=True)
class NodeDetail:
    """Full node content for show display."""

    node_id: str
    context: str
    node_type: str
    timestamp: str
    source: str
    creator: str
    body: str
    tags: list[str] = field(default_factory=list)
    related_to: list[str] = field(default_factory=list)
    meta: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistryResult:
    """Result of init_registry."""

    config_path: Path
    valid: bool
    errors: list[str] = field(default_factory=list)
    set_as_default: bool = False


@dataclass(frozen=True)
class PoolResult:
    """Result of init_pool."""

    pool_path: Path
    valid: bool
    errors: list[str] = field(default_factory=list)
    config_path: Path | None = None


@dataclass(frozen=True)
class RegistrySummary:
    """A registry entry as collected from a config file, for display."""

    registry_id: str
    name: str
    context: str
    config_path: Path


@dataclass(frozen=True)
class ConfigPathSummary:
    """Metadata about a single config file path in the discovery tree."""

    path: Path
    exists: bool
    is_global: bool
    registry_ids: list[str]


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

_REQUIRED_NODE_FIELDS = {
    "schema_version",
    "id",
    "timestamp",
    "source",
    "node_type",
    "context",
    "creator",
}

_VALID_NODE_TYPES = {"fixed", "live"}
_VALID_SCHEMA_VERSIONS = {"1"}
_VALID_STATUSES = {"active", "archived", "suppressed"}
_DEFAULT_STATUS = "active"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def extract_frontmatter(text: str) -> dict[str, object] | None:
    """Parse YAML frontmatter from a Markdown string.

    Frontmatter is the YAML block between the opening ``---`` and closing
    ``---`` at the top of the file.

    Args:
        text: Raw file contents.

    Returns:
        Parsed frontmatter as a dict, or None if no frontmatter is present.
    """
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    # parts[0] is empty string before first ---, parts[1] is YAML, parts[2] is body
    if len(parts) < 3:
        return None
    parsed = yaml.safe_load(parts[1])
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_node(frontmatter: dict[str, object]) -> ValidationResult:
    """Validate a node's frontmatter against the v1 schema.

    Args:
        frontmatter: Parsed YAML frontmatter from a node file.

    Returns:
        ValidationResult with valid=True and empty errors if compliant,
        or valid=False with a list of error messages if not.
    """
    errors = [
        f"missing required field: '{f}'"
        for f in sorted(_REQUIRED_NODE_FIELDS - frontmatter.keys())
    ]

    if "node_type" in frontmatter and frontmatter["node_type"] not in _VALID_NODE_TYPES:
        errors.append(
            f"invalid node_type: '{frontmatter['node_type']}'"
            f" (must be one of: {sorted(_VALID_NODE_TYPES)})"
        )

    if "schema_version" in frontmatter and frontmatter["schema_version"] not in _VALID_SCHEMA_VERSIONS:
        errors.append(
            f"invalid schema_version: '{frontmatter['schema_version']}'"
            f" (supported: {sorted(_VALID_SCHEMA_VERSIONS)})"
        )

    if "status" in frontmatter and frontmatter["status"] not in _VALID_STATUSES:
        errors.append(
            f"invalid status: '{frontmatter['status']}'"
            f" (must be one of: {sorted(_VALID_STATUSES)})"
        )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_registry(config: dict[str, object]) -> ValidationResult:
    """Validate a registry config dict for structural correctness.

    Checks that required registry fields are present, all declared pools have
    a ``context`` field, and pool names are unique.

    Args:
        config: Parsed registry config (from ``config.yaml``).

    Returns:
        ValidationResult with errors if the structure is invalid.
    """
    errors: list[str] = []
    registries = config.get("registries")
    if not registries or not isinstance(registries, dict):
        errors.append("missing 'registries' map in config")
        return ValidationResult(valid=False, errors=errors)

    for reg_id, reg_data in registries.items():
        if not isinstance(reg_data, dict):
            errors.append(f"registry '{reg_id}': must be a mapping")
            continue
        if "context" not in reg_data:
            errors.append(f"registry '{reg_id}': missing required field 'context'")

    pools = config.get("pools", {})
    if isinstance(pools, dict):
        seen: set[str] = set()
        for pool_name, pool_data in pools.items():
            if pool_name in seen:
                errors.append(f"duplicate pool name: '{pool_name}'")
            seen.add(pool_name)
            if isinstance(pool_data, dict) and "context" not in pool_data:
                errors.append(f"pool '{pool_name}': missing required field 'context'")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def generate_id(*, timestamp: str, source: str, context: str) -> str:
    """Generate a deterministic 12-character node ID.

    The ID is the first 12 hex characters of SHA-256 over the concatenation
    of timestamp, source, and context. Identical inputs always produce the
    same ID, enabling idempotency checks.

    Args:
        timestamp: ISO-8601 creation timestamp.
        source: Originating system (e.g. ``"cli"``, ``"slack"``).
        context: Human/LLM-readable context description.

    Returns:
        12-character lowercase hex string.
    """
    raw = f"{timestamp}{source}{context}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config(
    *,
    global_config_dir: Path,
    cwd: Path | None = None,
    overrides: dict[str, object] | None = None,
) -> AlphConfig:
    """Load and merge configuration from the global config and the cwd directory tree.

    Priority (highest wins): CLI overrides > cwd-nearest config > ... > global config > defaults.

    The config tree is:
    - ``global_config_dir/config.yaml`` — base layer, read first
    - Walk-up from ``cwd`` to root: each ``config.yaml`` found is applied in
      order from least specific (root) to most specific (cwd), so the cwd-nearest
      file wins for scalar values like ``creator`` and ``default_pool``.

    Registry entries accumulate across all files. When the same ID appears in
    multiple files, the most-specific (cwd-nearest) path wins. Registry entries
    in dict format ``{id: {context: ...}}`` (registry-home configs) are resolved
    to the containing directory as the registry home path.

    Args:
        global_config_dir: Directory containing the global ``config.yaml``
            (typically ``~/.config/alph/``).
        cwd: Starting directory for the upward walk. Defaults to no walk.
        overrides: Per-invocation overrides (from CLI flags).

    Returns:
        Merged AlphConfig.
    """
    merged: dict[str, object] = {}
    accumulated_registries: dict[str, str] = {}

    def _apply(config_path: Path) -> None:
        if not config_path.exists():
            logger.debug("config not found, skipping: %s", config_path)
            return
        logger.debug("applying config: %s", config_path)
        data = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(data, dict):
            return
        # Accumulate registries: path-string format goes in directly;
        # dict format (registry home config) uses the config file's parent as home.
        regs = data.get("registries", {})
        if isinstance(regs, dict):
            for k, v in regs.items():
                if isinstance(v, str):
                    accumulated_registries[str(k)] = v
                elif isinstance(v, dict):
                    accumulated_registries[str(k)] = str(config_path.parent)
        merged.update({k: v for k, v in data.items() if k != "registries"})

    # Global config is the base layer.
    global_config = global_config_dir / "config.yaml"
    _apply(global_config)

    if cwd is not None:
        # Collect paths from cwd up to root, then reverse so root→cwd order
        # means cwd is applied last (most specific wins).
        walk_paths: list[Path] = []
        current = Path(cwd)
        while True:
            p = current / "config.yaml"
            if p != global_config:
                walk_paths.append(p)
            parent = current.parent
            if parent == current:
                break
            current = parent
        for config_path in reversed(walk_paths):
            _apply(config_path)

    logger.debug(
        "config loaded: creator=%r default_registry=%r default_pool=%r registries=%s",
        merged.get("creator", ""),
        merged.get("default_registry", ""),
        merged.get("default_pool", ""),
        list(accumulated_registries.keys()),
    )

    if overrides:
        merged.update(overrides)

    return AlphConfig(
        creator=str(merged.get("creator", "")),
        auto_commit=bool(merged.get("auto_commit", False)),
        default_registry=str(merged.get("default_registry", "")),
        default_pool=str(merged.get("default_pool", "")),
        registries=accumulated_registries,
    )


def find_registry_config(
    registry_id_or_name: str,
    *,
    cfg: AlphConfig,
) -> tuple[str, Path] | None:
    """Find a registry by ID or name from a loaded config.

    Looks up the registry in ``cfg.registries`` (populated by ``load_config``).
    For name-based lookup, reads each registry's home ``config.yaml`` to check
    the declared name.

    Args:
        registry_id_or_name: Registry ID or human-readable name to look up.
        cfg: Merged config from ``load_config``.

    Returns:
        ``(actual_registry_id, home_path)`` if found, ``None`` otherwise.
    """
    # Fast path: lookup by ID.
    if registry_id_or_name in cfg.registries:
        home = Path(cfg.registries[registry_id_or_name])
        logger.debug("registry found by ID: %r -> %s", registry_id_or_name, home)
        return (registry_id_or_name, home)

    # Slow path: lookup by name — check each home config for a matching name.
    logger.debug("registry %r not found by ID, trying name lookup", registry_id_or_name)
    for reg_id, home_str in cfg.registries.items():
        home = Path(home_str)
        home_config = home / "config.yaml"
        if not home_config.exists():
            continue
        try:
            data = yaml.safe_load(home_config.read_text()) or {}
            if not isinstance(data, dict):
                continue
            regs = data.get("registries", {})
            if not isinstance(regs, dict) or reg_id not in regs:
                continue
            entry = regs[reg_id]
            if isinstance(entry, dict) and entry.get("name") == registry_id_or_name:
                return (reg_id, home)
        except Exception:
            pass

    return None


def collect_registries(
    *,
    cfg: AlphConfig,
) -> list[RegistrySummary]:
    """Return display summaries for all registries in the loaded config.

    For each registry in ``cfg.registries``, reads the registry home
    ``config.yaml`` to extract context and name.

    Args:
        cfg: Merged config from ``load_config``.

    Returns:
        List of RegistrySummary, one per entry in cfg.registries.
    """
    results: list[RegistrySummary] = []
    for reg_id, home_str in cfg.registries.items():
        home = Path(home_str)
        name = ""
        context = ""
        config_path = home / "config.yaml"
        if config_path.exists():
            try:
                data = yaml.safe_load(config_path.read_text()) or {}
                if isinstance(data, dict):
                    regs = data.get("registries", {})
                    if isinstance(regs, dict) and reg_id in regs:
                        entry = regs[reg_id]
                        if isinstance(entry, dict):
                            name = str(entry.get("name", ""))
                            context = str(entry.get("context", ""))
            except Exception:
                pass
        results.append(RegistrySummary(
            registry_id=reg_id,
            name=name,
            context=context,
            config_path=config_path,
        ))
    return results


def list_config_paths(
    *,
    global_config_dir: Path,
    cwd: Path,
) -> list[ConfigPathSummary]:
    """Return all config file paths in the discovery tree, with metadata.

    The list mirrors the order ``load_config`` applies configs: global first
    (base), then the walk-up from root → cwd (most specific last). Each entry
    reports whether the file exists on disk and which registry IDs it declares.

    Args:
        global_config_dir: Global alph config directory.
        cwd: Starting directory for the upward walk.

    Returns:
        List of ConfigPathSummary, one per path that load_config would check.
    """
    global_config = global_config_dir / "config.yaml"

    # Collect cwd walk paths (cwd → root), then reverse to get root → cwd.
    walk_paths: list[Path] = []
    current = Path(cwd)
    while True:
        p = current / "config.yaml"
        if p != global_config:
            walk_paths.append(p)
        parent = current.parent
        if parent == current:
            break
        current = parent

    ordered: list[Path] = [global_config, *reversed(walk_paths)]

    def _registry_ids(path: Path) -> list[str]:
        if not path.exists():
            return []
        try:
            data = yaml.safe_load(path.read_text()) or {}
            if not isinstance(data, dict):
                return []
            regs = data.get("registries", {})
            return [str(k) for k in regs] if isinstance(regs, dict) else []
        except Exception:
            return []

    return [
        ConfigPathSummary(
            path=p,
            exists=p.exists(),
            is_global=(p == global_config),
            registry_ids=_registry_ids(p),
        )
        for p in ordered
    ]


def default_global_config_text() -> str:
    """Return a commented YAML template for a new global alph config file.

    All standard keys are included with their defaults and a description
    comment above each one. Intended for first-time setup and ``alph config
    show`` output when no config file exists yet.

    Returns:
        Multi-line string of valid YAML with inline documentation comments.
    """
    return """\
# alph global configuration
# Location: ~/.config/alph/config.yaml
#
# Run 'alph config list' to see all config files in the discovery tree.
# Run 'alph registry init' to create a registry and populate this file.

# Your email address used as the default creator for new nodes.
# Omitting --creator on 'alph add' requires this to be set.
# Default: "" (empty — --creator must be supplied on each command)
creator: ""

# The registry ID that 'alph add' and 'alph list' resolve when
# no --pool flag is given. Set automatically by 'alph registry init'
# when no default exists yet.
# Default: "" (no default registry)
default_registry: ""

# The pool name within default_registry used when no --pool flag is given.
# Set automatically by 'alph pool init' for the first pool in the default registry.
# Default: "" (no default pool)
default_pool: ""

# Registry map: ID → path to the registry home directory.
# Populated automatically by 'alph registry init'.
# You can add entries manually for registries not managed by this machine.
# Default: {} (empty map)
registries: {}

# When true, 'alph add' commits each new node to git automatically.
# Requires the pool directory to be inside a git repository.
# Default: false
auto_commit: false
"""


def resolve_default_pool(config: AlphConfig) -> Path | None:
    """Resolve the default pool path from config.

    Returns the path ``registries[default_registry] / default_pool`` when all
    three are set, or ``None`` if any piece is missing.
    """
    if not config.default_registry or not config.default_pool:
        return None
    registry_path_str = config.registries.get(config.default_registry)
    if not registry_path_str:
        return None
    return Path(registry_path_str) / config.default_pool


# ---------------------------------------------------------------------------
# Registry and pool initialisation
# ---------------------------------------------------------------------------


def init_registry(
    *,
    home: Path,
    registry_id: str,
    context: str,
    name: str = "",
    global_config_dir: Path | None = None,
) -> RegistryResult:
    """Create a registry home directory and write its local config.yaml.

    When ``global_config_dir`` is provided, the registry is also registered
    in the global config (``registries`` map) so ``load_config`` can resolve
    it. If no ``default_registry`` is set globally, this registry becomes the
    default.

    Args:
        home: Directory to create as the registry root. Pool subdirectories
            will be created inside it. A ``config.yaml`` with the registry
            metadata is written here.
        registry_id: Machine identifier for the registry.
        context: Human/LLM-readable description.
        name: Optional human-readable name.
        global_config_dir: Global alph config directory. When provided, the
            registry path is added to the global ``registries`` map and
            ``default_registry`` is set if none exists.

    Returns:
        RegistryResult with config_path, validation outcome, and set_as_default.
    """
    home.mkdir(parents=True, exist_ok=True)

    registry_entry: dict[str, object] = {"context": context}
    if name:
        registry_entry["name"] = name

    local_config: dict[str, object] = {
        "registries": {registry_id: registry_entry},
        "pools": {},
    }
    config_path = home / "config.yaml"
    config_path.write_text(yaml.dump(local_config, default_flow_style=False, allow_unicode=True))

    set_as_default = False
    if global_config_dir is not None:
        global_config_dir.mkdir(parents=True, exist_ok=True)
        global_config_path = global_config_dir / "config.yaml"
        global_data: dict[str, object] = {}
        if global_config_path.exists():
            loaded = yaml.safe_load(global_config_path.read_text()) or {}
            if isinstance(loaded, dict):
                global_data = loaded

        # Register this registry's home path so load_config can build the map.
        global_registries = global_data.get("registries", {})
        if not isinstance(global_registries, dict):
            global_registries = {}
        global_registries[registry_id] = str(home)
        global_data["registries"] = global_registries

        # Set as default if none exists.
        if not global_data.get("default_registry"):
            global_data["default_registry"] = registry_id
            set_as_default = True

        global_config_path.write_text(
            yaml.dump(global_data, default_flow_style=False, allow_unicode=True)
        )

    validation = validate_registry(local_config)
    return RegistryResult(
        config_path=config_path,
        valid=validation.valid,
        errors=validation.errors,
        set_as_default=set_as_default,
    )


def init_pool(
    *,
    registry_id: str,
    name: str,
    context: str,
    layout: str = "subdirectory",
    cwd: Path,
    global_config_dir: Path,
    bootstrap: bool = False,
    registry_context: str = "",
) -> PoolResult:
    """Create a pool and register it in the registry config.

    Looks up the registry by ID or name using ``find_registry_config``, which
    walks up from ``cwd`` and falls back to ``global_config_dir``. When
    ``bootstrap=True`` and the registry is not found, it is created at ``cwd``
    before the pool is built.

    Args:
        registry_id: Registry ID or name to attach the pool to.
        name: Pool name (machine identifier, used as directory name).
        context: Human/LLM-readable description.
        layout: ``"subdirectory"`` (default) or ``"repo"``.
        cwd: Starting directory for the registry config walk-up.
        global_config_dir: Global alph config directory (fallback for lookup).
        bootstrap: When True, create the registry at ``cwd`` if not found.
        registry_context: Context for the bootstrapped registry.

    Returns:
        PoolResult with pool_path, config_path, and validation outcome.
        Returns invalid result (with errors) if the registry is not found and
        bootstrap is False.
    """
    cfg = load_config(global_config_dir=global_config_dir, cwd=cwd)
    found = find_registry_config(registry_id, cfg=cfg)

    if found is None:
        if bootstrap:
            init_registry(
                home=cwd,
                registry_id=registry_id,
                context=registry_context or f"Registry {registry_id}",
                global_config_dir=global_config_dir,
            )
            cfg = load_config(global_config_dir=global_config_dir, cwd=cwd)
            found = find_registry_config(registry_id, cfg=cfg)

        if found is None:
            return PoolResult(
                pool_path=cwd / name,
                valid=False,
                errors=[
                    f"registry '{registry_id}' not found. "
                    "Run 'alph registry init' first, or use 'alph registry list' "
                    "to see known registries."
                ],
            )

    actual_reg_id, registry_path = found
    config_file = registry_path / "config.yaml"

    pool_path = registry_path / name
    for subdir in ("snapshots", "pointers", ".alph"):
        (pool_path / subdir).mkdir(parents=True, exist_ok=True)

    # Update the registry config to include this pool.
    config: dict[str, object] = {}
    if config_file.exists():
        loaded = yaml.safe_load(config_file.read_text()) or {}
        if isinstance(loaded, dict):
            config = loaded

    pools = config.get("pools", {})
    if not isinstance(pools, dict):
        pools = {}
    pools[name] = {"context": context, "layout": layout, "path": f"./{name}"}
    config["pools"] = pools
    config_file.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True))

    # If this is the default registry and no default_pool is set, register it.
    global_config_path = global_config_dir / "config.yaml"
    if global_config_path.exists():
        global_data: dict[str, object] = yaml.safe_load(global_config_path.read_text()) or {}
        if isinstance(global_data, dict):
            default_reg = str(global_data.get("default_registry", ""))
            if default_reg == actual_reg_id and not global_data.get("default_pool"):
                global_data["default_pool"] = name
                global_config_path.write_text(
                    yaml.dump(global_data, default_flow_style=False, allow_unicode=True)
                )

    validation = validate_registry(config)
    return PoolResult(
        pool_path=pool_path,
        valid=validation.valid,
        errors=validation.errors,
        config_path=config_file,
    )


# ---------------------------------------------------------------------------
# Node creation
# ---------------------------------------------------------------------------


def check_idempotency(pool_path: Path, node_id: str) -> ExistingNode | None:
    """Check whether a node with the given ID already exists in a pool.

    Scans both ``snapshots/`` and ``pointers/`` subdirectories for a file
    whose frontmatter ``id`` matches *node_id*.

    Args:
        pool_path: Root directory of the pool.
        node_id: 12-character node ID to search for.

    Returns:
        ExistingNode with creator and timestamp if found, None otherwise.
    """
    for subdir in ("snapshots", "pointers"):
        directory = pool_path / subdir
        if not directory.exists():
            continue
        for node_file in directory.glob("*.md"):
            frontmatter = extract_frontmatter(node_file.read_text())
            if frontmatter and frontmatter.get("id") == node_id:
                return ExistingNode(
                    creator=str(frontmatter["creator"]),
                    timestamp=str(frontmatter["timestamp"]),
                )
    return None


def create_node(
    *,
    pool_path: Path,
    source: str,
    node_type: str,
    context: str,
    creator: str,
    timestamp: str | None = None,
    content: str = "",
    status: str | None = None,
    tags: list[str] | None = None,
    related_to: list[str] | None = None,
    meta: dict[str, object] | None = None,
    auto_commit: bool = False,
) -> NodeResult:
    """Create a context node and write it to the pool.

    Generates a deterministic ID, checks for duplicates, then writes a
    Markdown file with YAML frontmatter to ``snapshots/`` (fixed nodes) or
    ``pointers/`` (live nodes).

    Args:
        pool_path: Root directory of the pool.
        source: Originating system (e.g. ``"cli"``).
        node_type: ``"fixed"`` or ``"live"``.
        context: Human/LLM-readable description.
        creator: Email address of the creator.
        timestamp: ISO-8601 timestamp; defaults to now (UTC).
        content: Optional Markdown body below the frontmatter.
        tags: Optional semantic labels.
        related_to: Optional list of cross-reference strings.
        meta: Optional source-specific key-value pairs.

    Returns:
        NodeResult with node_id and path. If a duplicate is detected,
        ``duplicate=True`` and ``existing_creator`` are set instead of
        writing a new file.
    """
    resolved_timestamp = timestamp or datetime.now(UTC).isoformat()
    node_id = generate_id(timestamp=resolved_timestamp, source=source, context=context)
    logger.debug("create_node: id=%s type=%s pool=%s", node_id, node_type, pool_path)

    existing = check_idempotency(pool_path, node_id)
    if existing is not None:
        logger.debug("create_node: duplicate detected, created by %r", existing.creator)
        subdir = "snapshots" if node_type == "fixed" else "pointers"
        path = pool_path / subdir / f"{node_id}.md"
        return NodeResult(
            node_id=node_id,
            path=path,
            duplicate=True,
            existing_creator=existing.creator,
        )

    subdir = "snapshots" if node_type == "fixed" else "pointers"
    directory = pool_path / subdir
    directory.mkdir(parents=True, exist_ok=True)

    frontmatter: dict[str, object] = {
        "schema_version": "1",
        "id": node_id,
        "timestamp": resolved_timestamp,
        "source": source,
        "node_type": node_type,
        "context": context,
        "creator": creator,
    }
    if status is not None:
        frontmatter["status"] = status
    if tags:
        frontmatter["tags"] = tags
    if related_to:
        frontmatter["related_to"] = related_to
    if meta:
        frontmatter["meta"] = meta

    # Use default_flow_style=False for readable block style; allow_unicode for
    # non-ASCII context text. Timestamp is a str value — yaml.dump quotes it.
    frontmatter_text = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    body = f"---\n{frontmatter_text}---\n"
    if content:
        body += f"\n{content}\n"

    path = directory / f"{node_id}.md"
    path.write_text(body)

    if auto_commit:
        try:
            subprocess.run(
                ["git", "-C", str(pool_path), "add", str(path)],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(pool_path), "commit", "-m",
                 f"alph: add {node_type} node {node_id}"],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass  # not a git repo or git unavailable — silently skip

    return NodeResult(node_id=node_id, path=path)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def list_nodes(
    pool_path: Path,
    *,
    include_statuses: set[str] | None = None,
) -> list[NodeSummary]:
    """List nodes in a pool as lightweight summaries, filtered by status.

    Default behaviour (``include_statuses=None``) returns only active nodes
    (those with ``status: active`` or no status field). Pass an explicit set
    to expand the results — e.g. ``{"active", "archived"}`` or the full set
    ``{"active", "archived", "suppressed"}`` for everything.

    Args:
        pool_path: Root directory of the pool.
        include_statuses: Set of status values to include. None means active only.

    Returns:
        List of NodeSummary, one per matching node file.
    """
    allowed = include_statuses if include_statuses is not None else {_DEFAULT_STATUS}
    summaries: list[NodeSummary] = []
    for subdir in ("snapshots", "pointers"):
        directory = pool_path / subdir
        if not directory.exists():
            continue
        for node_file in sorted(directory.glob("*.md")):
            frontmatter = extract_frontmatter(node_file.read_text())
            if not frontmatter:
                continue
            node_status = str(frontmatter.get("status", _DEFAULT_STATUS))
            if node_status not in allowed:
                continue
            summaries.append(
                NodeSummary(
                    node_id=str(frontmatter.get("id", "")),
                    context=str(frontmatter.get("context", "")),
                    node_type=str(frontmatter.get("node_type", "")),
                    timestamp=str(frontmatter.get("timestamp", "")),
                    source=str(frontmatter.get("source", "")),
                    status=node_status,
                )
            )
    return summaries


def show_node(pool_path: Path, node_id: str) -> NodeDetail | None:
    """Return the full content of a node by ID.

    Args:
        pool_path: Root directory of the pool.
        node_id: 12-character node ID to look up.

    Returns:
        NodeDetail with all frontmatter fields and body, or None if not found.
    """
    for subdir in ("snapshots", "pointers"):
        directory = pool_path / subdir
        if not directory.exists():
            continue
        for node_file in directory.glob("*.md"):
            text = node_file.read_text()
            frontmatter = extract_frontmatter(text)
            if not frontmatter or frontmatter.get("id") != node_id:
                continue
            # Body is the text after the closing --- delimiter
            parts = text.split("---", 2)
            body = parts[2].strip() if len(parts) == 3 else ""
            tags = frontmatter.get("tags", [])
            related_to = frontmatter.get("related_to", [])
            meta = frontmatter.get("meta", {})
            return NodeDetail(
                node_id=node_id,
                context=str(frontmatter.get("context", "")),
                node_type=str(frontmatter.get("node_type", "")),
                timestamp=str(frontmatter.get("timestamp", "")),
                source=str(frontmatter.get("source", "")),
                creator=str(frontmatter.get("creator", "")),
                body=body,
                tags=[str(t) for t in tags] if isinstance(tags, list) else [],
                related_to=[str(r) for r in related_to] if isinstance(related_to, list) else [],
                meta=dict(meta) if isinstance(meta, dict) else {},
            )
    return None


# ---------------------------------------------------------------------------
# Timeline state
# ---------------------------------------------------------------------------

_TIMELINE_STATE_FILE = ".timeline-state.json"


def load_state(pool_path: Path) -> TimelineState:
    """Load timeline state from a pool's state file.

    Args:
        pool_path: Root directory of the pool.

    Returns:
        Persisted TimelineState, or a default empty state if no file exists.
    """
    state_file = pool_path / _TIMELINE_STATE_FILE
    if not state_file.exists():
        return TimelineState()
    data = json.loads(state_file.read_text())
    return TimelineState(
        last_loaded=data.get("last_loaded"),
        node_verified=data.get("node_verified", {}),
    )


def update_state(
    pool_path: Path,
    state: TimelineState,
    *,
    last_loaded: str | None = None,
    node_verified: dict[str, str] | None = None,
) -> TimelineState:
    """Persist updated timeline state for a pool.

    Merges the provided updates onto the existing state and writes the result.

    Args:
        pool_path: Root directory of the pool.
        state: Current state to update from.
        last_loaded: New last_loaded timestamp, if updating.
        node_verified: New node_verified mapping, if updating.

    Returns:
        The new persisted TimelineState.
    """
    new_state = TimelineState(
        last_loaded=last_loaded if last_loaded is not None else state.last_loaded,
        node_verified=node_verified if node_verified is not None else state.node_verified,
    )
    state_file = pool_path / _TIMELINE_STATE_FILE
    state_file.write_text(
        json.dumps(
            {"last_loaded": new_state.last_loaded, "node_verified": new_state.node_verified},
            indent=2,
        )
    )
    return new_state
