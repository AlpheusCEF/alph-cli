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
# Reserved names — cannot be used as registry IDs or pool names
# ---------------------------------------------------------------------------

RESERVED_NAMES: frozenset[str] = frozenset({"all"})

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """Result of a validation check."""

    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class RegistryEntry:
    """A registry definition as stored in the config.

    All metadata (context, name, pools) lives here — there is no separate
    per-registry config.yaml. The ``pool_home`` directory is where pool
    subdirectories are created. For remote registries, ``pool_home`` is a
    git remote URL (optionally with ``:/subpath`` suffix).
    """

    pool_home: str
    context: str = ""
    name: str = ""
    pools: dict[str, object] = field(default_factory=dict)
    mode: str = ""  # "" = auto (ro for remote, rw for local), "ro", or "rw"
    clone_path: str = ""  # user-specified local clone dir (rw remote only)
    auto_push: bool = False  # push after commit (rw remote only)
    auto_pull: bool = False  # pull before read (rw remote only)
    branch: str = ""  # git branch for RO reads and RW clone checkout


@dataclass(frozen=True)
class AlphConfig:
    """Merged configuration from global and cwd-walk config layers.

    registries maps registry ID -> RegistryEntry (home path + metadata).
    Accumulates across the config cascade — later files add to (or override
    individual entries in) the map rather than replacing it wholesale.
    """

    creator: str = ""
    auto_commit: bool = False
    default_registry: str = ""
    default_pool: str = ""
    registries: dict[str, RegistryEntry] = field(default_factory=dict)


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
    """A registry entry as collected from config, for display."""

    registry_id: str
    name: str
    context: str
    home_path: Path


@dataclass(frozen=True)
class PoolSummary:
    """A pool entry for display — may be configured or discovered on disk."""

    name: str
    context: str
    pool_type: str
    path: Path
    source: str = "configured"  # "configured" or "discovered"


@dataclass(frozen=True)
class ConfigPathSummary:
    """Metadata about a single config file path in the discovery tree."""

    path: Path
    exists: bool
    is_global: bool
    registry_ids: list[str]


@dataclass(frozen=True)
class RemoteRegistryRef:
    """Parsed remote git URL for a registry pool_home."""

    remote_url: str  # e.g. git@github.com:AlpheusCEF/repo.git
    subpath: str  # e.g. "registry" or "" if root
    original: str  # raw pool_home string as configured


# ---------------------------------------------------------------------------
# Remote registry detection
# ---------------------------------------------------------------------------

_REMOTE_PREFIXES = ("git@", "ssh://", "git://", "http://", "https://")


def is_remote_registry(pool_home: str) -> bool:
    """Return True if pool_home is a remote git URL rather than a local path."""
    if not pool_home:
        return False
    return any(pool_home.startswith(p) for p in _REMOTE_PREFIXES)


def parse_remote_registry(pool_home: str) -> RemoteRegistryRef:
    """Parse a remote pool_home into its URL and subpath components.

    Format: ``<git-remote-url>:/<subpath>`` where ``:/subpath`` is optional.
    For SSH ``git@`` URLs the ``:/`` delimiter separates the URL from the
    subpath. For protocol URLs (https://, ssh://, git://) the ``.git:/``
    boundary is used.

    Raises:
        ValueError: If pool_home is not a remote URL.
    """
    if not is_remote_registry(pool_home):
        raise ValueError(f"not a remote registry URL: {pool_home!r}")

    # Split on ":/" that follows .git — this is the subpath delimiter.
    # For git@ URLs: git@host:org/repo.git:/subpath
    # For https:// URLs: https://host/org/repo.git:/subpath
    if ".git:/" in pool_home:
        url_part, subpath = pool_home.split(".git:/", 1)
        remote_url = url_part + ".git"
        # Strip leading slash from subpath if present
        subpath = subpath.lstrip("/")
    else:
        remote_url = pool_home
        subpath = ""

    return RemoteRegistryRef(
        remote_url=remote_url,
        subpath=subpath,
        original=pool_home,
    )


def effective_mode(entry: RegistryEntry) -> str:
    """Return the effective access mode for a registry entry.

    Local registries are always ``"rw"``. Remote registries default to
    ``"ro"`` unless explicitly configured as ``"rw"``.
    """
    if not is_remote_registry(entry.pool_home):
        return "rw"
    if entry.mode == "rw":
        return "rw"
    return "ro"


def check_git_state(path: Path) -> ValidationResult:
    """Check that a directory is a healthy git repo for auto_pull/auto_push.

    Checks:
    - Is a git repository
    - Has at least one remote configured
    - Working tree is clean (no uncommitted changes)
    """
    errors: list[str] = []

    if not (path / ".git").is_dir():
        return ValidationResult(valid=False, errors=["not a git repository"])

    # Check for remotes.
    remotes = subprocess.run(
        ["git", "-C", str(path), "remote"],
        capture_output=True, text=True, timeout=10,
    )
    if remotes.returncode != 0 or not remotes.stdout.strip():
        errors.append("no remote configured — auto_pull/auto_push require a remote")

    # Check for uncommitted changes.
    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    if status.returncode == 0 and status.stdout.strip():
        errors.append("uncommitted changes in working tree")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


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

_VALID_NODE_TYPES = {"snapshot", "snap", "live"}
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
    """Validate a global config dict's registry declarations for structural correctness.

    Checks that required registry fields are present and all declared pools
    have a ``context`` field. Registries stored as plain path strings are
    accepted without further validation (they have no metadata to check).

    Args:
        config: Parsed global config dict (from ``~/.config/alph/config.yaml``
            or a local override). Expects ``registries`` to be a mapping of
            ``id -> {home, context, [name], [pools]}``.

    Returns:
        ValidationResult with errors if the structure is invalid.
    """
    errors: list[str] = []
    registries = config.get("registries")
    if not registries or not isinstance(registries, dict):
        errors.append("missing 'registries' map in config")
        return ValidationResult(valid=False, errors=errors)

    for reg_id, reg_data in registries.items():
        if isinstance(reg_data, str):
            continue  # path-string format — valid, no metadata to check
        if not isinstance(reg_data, dict):
            errors.append(f"registry '{reg_id}': must be a mapping or path string")
            continue
        if "context" not in reg_data:
            errors.append(f"registry '{reg_id}': missing required field 'context'")
        pools = reg_data.get("pools", {})
        if isinstance(pools, dict):
            seen: set[str] = set()
            for pool_name, pool_data in pools.items():
                if pool_name in seen:
                    errors.append(f"registry '{reg_id}': duplicate pool name: '{pool_name}'")
                seen.add(pool_name)
                if isinstance(pool_data, dict) and "context" not in pool_data:
                    errors.append(
                        f"registry '{reg_id}': pool '{pool_name}': missing required field 'context'"
                    )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def generate_id(*, source: str, context: str) -> str:
    """Generate a deterministic 12-character node ID.

    The ID is the first 12 hex characters of SHA-256 over the concatenation
    of source and context. Timestamp is intentionally excluded so that
    submitting the same context twice (e.g. re-running a CLI command)
    produces the same ID and triggers the duplicate check.

    Args:
        source: Originating system (e.g. ``"cli"``, ``"slack"``).
        context: Human/LLM-readable context description.

    Returns:
        12-character lowercase hex string.
    """
    raw = f"{source}{context}"
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
    accumulated_registries: dict[str, RegistryEntry] = {}

    def _apply(config_path: Path) -> None:
        if not config_path.exists():
            logger.debug("config not found, skipping: %s", config_path)
            return
        logger.debug("applying config: %s", config_path)
        data = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(data, dict):
            return
        # Accumulate registries into RegistryEntry objects.
        # - String value: pool_home path only (no metadata).
        # - Dict with "pool_home" key: full entry written by init_registry.
        # - Dict with legacy "home" key: backwards compat — treat as pool_home.
        # - Dict without either key: legacy home-config format; use config_path.parent as pool_home.
        regs = data.get("registries", {})
        if isinstance(regs, dict):
            for k, v in regs.items():
                if isinstance(v, str):
                    accumulated_registries[str(k)] = RegistryEntry(pool_home=v)
                elif isinstance(v, dict):
                    home_val = str(v.get("pool_home") or v.get("home") or config_path.parent)
                    pools_raw = v.get("pools", {})
                    mode_val = str(v.get("mode", ""))
                    # Smart defaults: remote RW registries default auto_pull
                    # and auto_push to True.  Explicit values always win.
                    _is_rw_remote = (
                        is_remote_registry(home_val) and mode_val == "rw"
                    )
                    _rw_default = _is_rw_remote
                    accumulated_registries[str(k)] = RegistryEntry(
                        pool_home=home_val,
                        context=str(v.get("context", "")),
                        name=str(v.get("name", "")),
                        pools=dict(pools_raw) if isinstance(pools_raw, dict) else {},
                        mode=mode_val,
                        clone_path=str(v.get("clone_path", "")),
                        auto_push=bool(v["auto_push"]) if "auto_push" in v else _rw_default,
                        auto_pull=bool(v["auto_pull"]) if "auto_pull" in v else _rw_default,
                        branch=str(v.get("branch", "")),
                    )
        merged.update({k: v for k, v in data.items() if k != "registries"})

    # Global config is the base layer.
    global_config = (global_config_dir / "config.yaml").resolve()
    _apply(global_config)

    if cwd is not None:
        # Collect paths from cwd up to root, then reverse so root→cwd order
        # means cwd is applied last (most specific wins).
        walk_paths: list[Path] = []
        seen: set[Path] = {global_config}
        current = Path(cwd).resolve()
        while True:
            p = current / "config.yaml"
            if p not in seen:
                seen.add(p)
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


def _is_pool_dir(path: Path) -> bool:
    """Return True if *path* looks like a pool (has snapshots/ or live/)."""
    return (path / "snapshots").is_dir() or (path / "live").is_dir()


def list_pools(
    registry_id_or_name: str,
    *,
    cfg: AlphConfig,
) -> list[PoolSummary] | None:
    """Return display summaries for all pools in a registry.

    Combines configured pools from ``cfg.registries`` with pools
    discovered on disk (subdirectories of ``pool_home`` containing
    ``snapshots/`` or ``live/``).  Discovered pools that are not in
    the config are included with ``source="discovered"``.

    For remote registries whose ``pool_home`` is a URL, only
    configured pools are returned (discovery requires a provider and
    is handled by the CLI layer).

    Returns ``None`` if the registry is not found.
    """
    found = find_registry_config(registry_id_or_name, cfg=cfg)
    if found is None:
        return None
    reg_id, home = found
    entry = cfg.registries[reg_id]

    # Configured pools.
    configured_names: set[str] = set()
    summaries: list[PoolSummary] = []
    for pool_name, pool_data in entry.pools.items():
        if not isinstance(pool_data, dict):
            continue
        configured_names.add(pool_name)
        summaries.append(
            PoolSummary(
                name=pool_name,
                context=str(pool_data.get("context", "")),
                pool_type=str(pool_data.get("type", "subdir")),
                path=home / pool_name,
                source="configured",
            )
        )

    # Discover pools on disk for local registries.
    if not is_remote_registry(entry.pool_home) and home.is_dir():
        for child in sorted(home.iterdir()):
            if child.name in configured_names:
                continue
            if child.is_dir() and _is_pool_dir(child):
                summaries.append(
                    PoolSummary(
                        name=child.name,
                        context="",
                        pool_type="subdir",
                        path=child,
                        source="discovered",
                    )
                )

    return summaries


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
        entry = cfg.registries[registry_id_or_name]
        home = Path(entry.pool_home)
        logger.debug("registry found by ID: %r -> %s", registry_id_or_name, home)
        return (registry_id_or_name, home)

    # Slow path: lookup by name — check entry.name in loaded cfg (no file I/O needed).
    logger.debug("registry %r not found by ID, trying name lookup", registry_id_or_name)
    for reg_id, entry in cfg.registries.items():
        if entry.name == registry_id_or_name:
            logger.debug("registry found by name: %r -> %s", registry_id_or_name, entry.pool_home)
            return (reg_id, Path(entry.pool_home))

    return None


def collect_registries(
    *,
    cfg: AlphConfig,
) -> list[RegistrySummary]:
    """Return display summaries for all registries in the loaded config.

    Reads directly from ``cfg.registries`` — no additional file I/O required
    because ``load_config`` already populated the RegistryEntry metadata.

    Args:
        cfg: Merged config from ``load_config``.

    Returns:
        List of RegistrySummary, one per entry in cfg.registries.
    """
    return [
        RegistrySummary(
            registry_id=reg_id,
            name=entry.name,
            context=entry.context,
            home_path=Path(entry.pool_home),
        )
        for reg_id, entry in cfg.registries.items()
    ]


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
    global_config = (global_config_dir / "config.yaml").resolve()

    # Collect cwd walk paths (cwd → root), then reverse to get root → cwd.
    walk_paths: list[Path] = []
    seen: set[Path] = {global_config}
    current = Path(cwd).resolve()
    while True:
        p = current / "config.yaml"
        if p not in seen:
            seen.add(p)
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
    entry = config.registries.get(config.default_registry)
    if entry is None:
        return None
    return Path(entry.pool_home) / config.default_pool


def resolve_pool_name(name: str, cfg: AlphConfig) -> Path | None:
    """Resolve a pool name to its filesystem path via the registry config.

    Checks the default registry first, then all other known registries.
    Pool path is always ``registry_home / pool_name`` — the ``path`` field
    in pool metadata is ignored (it is redundant by convention).

    Args:
        name: Pool name to look up.
        cfg: Merged config from ``load_config``.

    Returns:
        Absolute path to the pool directory, or None if not found.
    """
    registries_ordered = list(cfg.registries.items())
    if cfg.default_registry:
        registries_ordered.sort(key=lambda kv: 0 if kv[0] == cfg.default_registry else 1)

    for _reg_id, entry in registries_ordered:
        if isinstance(entry.pools, dict) and name in entry.pools:
            return Path(entry.pool_home) / name
    return None


# ---------------------------------------------------------------------------
# Registry and pool initialisation
# ---------------------------------------------------------------------------


def init_registry(
    *,
    pool_home: Path,
    registry_id: str,
    context: str,
    name: str = "",
    mode: str = "",
    clone_path: str = "",
    branch: str = "",
    auto_push: bool | None = None,
    auto_pull: bool | None = None,
    global_config_dir: Path,
) -> RegistryResult:
    """Create a registry and register it in the global config.

    The registry definition (context, name, pools, and remote options) is
    written into the global config's ``registries`` map. For local registries,
    the ``pool_home`` directory is created on disk. For remote registries
    (git URLs), no directory is created.

    Args:
        pool_home: Directory or git remote URL (e.g.
            ``git@github.com:org/repo.git:/subpath``).
        registry_id: Machine identifier for the registry.
        context: Human/LLM-readable description.
        name: Optional human-readable name.
        mode: Access mode: ``"ro"`` or ``"rw"``. Empty string means auto
            (ro for remote, rw for local).
        clone_path: Local directory for RW clone (remote registries only).
        branch: Git branch for RO reads and RW clone checkout.
        auto_push: Push after commit. ``None`` means use smart default.
        auto_pull: Pull before read. ``None`` means use smart default.
        global_config_dir: Global alph config directory.

    Returns:
        RegistryResult with config_path pointing to the global config,
        validation outcome, and set_as_default.
    """
    if registry_id in RESERVED_NAMES:
        return RegistryResult(
            valid=False,
            errors=[f"'{registry_id}' is a reserved name and cannot be used as a registry ID"],
            config_path=global_config_dir / "config.yaml",
            set_as_default=False,
        )
    if not is_remote_registry(str(pool_home)):
        pool_home.mkdir(parents=True, exist_ok=True)
    global_config_dir.mkdir(parents=True, exist_ok=True)
    global_config_path = global_config_dir / "config.yaml"

    global_data: dict[str, object] = {}
    if global_config_path.exists():
        loaded = yaml.safe_load(global_config_path.read_text()) or {}
        if isinstance(loaded, dict):
            global_data = loaded

    global_registries = global_data.get("registries", {})
    if not isinstance(global_registries, dict):
        global_registries = {}

    # Write rich dict entry: pool_home path + metadata.
    # Only include optional fields when explicitly set to keep config clean.
    registry_entry: dict[str, object] = {"pool_home": str(pool_home), "context": context}
    if name:
        registry_entry["name"] = name
    if mode:
        registry_entry["mode"] = mode
    if clone_path:
        registry_entry["clone_path"] = clone_path
    if branch:
        registry_entry["branch"] = branch
    if auto_push is not None:
        registry_entry["auto_push"] = auto_push
    if auto_pull is not None:
        registry_entry["auto_pull"] = auto_pull
    global_registries[registry_id] = registry_entry
    global_data["registries"] = global_registries

    set_as_default = False
    if not global_data.get("default_registry"):
        global_data["default_registry"] = registry_id
        set_as_default = True

    global_config_path.write_text(
        yaml.dump(global_data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    )

    logger.debug("init_registry: wrote %s to %s", registry_id, global_config_path)
    validation = validate_registry(global_data)
    return RegistryResult(
        config_path=global_config_path,
        valid=validation.valid,
        errors=validation.errors,
        set_as_default=set_as_default,
    )


def init_pool(
    *,
    registry_id: str,
    name: str,
    context: str,
    pool_type: str = "subdir",
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
        pool_type: ``"subdir"`` (default, pool is a subdirectory of the registry home)
            or ``"repo"`` (pool is a standalone git repository).
        cwd: Starting directory for the registry config walk-up.
        global_config_dir: Global alph config directory (fallback for lookup).
        bootstrap: When True, create the registry at ``cwd`` if not found.
        registry_context: Context for the bootstrapped registry.

    Returns:
        PoolResult with pool_path, config_path, and validation outcome.
        Returns invalid result (with errors) if the registry is not found and
        bootstrap is False.
    """
    if name in RESERVED_NAMES:
        return PoolResult(
            valid=False,
            errors=[f"'{name}' is a reserved name and cannot be used as a pool name"],
            pool_path=Path(),
            config_path=global_config_dir / "config.yaml",
        )
    cfg = load_config(global_config_dir=global_config_dir, cwd=cwd)
    found = find_registry_config(registry_id, cfg=cfg)

    if found is None:
        if bootstrap:
            init_registry(
                pool_home=cwd,
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

    actual_reg_id, registry_home = found

    # Remote registry handling: resolve actual filesystem path.
    entry = cfg.registries.get(actual_reg_id)
    if entry and is_remote_registry(entry.pool_home):
        mode = effective_mode(entry)
        if mode == "ro":
            return PoolResult(
                pool_path=Path(),
                valid=False,
                errors=[
                    f"registry '{actual_reg_id}' is read-only. "
                    "Set mode: rw in config to enable writes.",
                ],
                config_path=global_config_dir / "config.yaml",
            )
        # RW remote: resolve clone_path + subpath.
        ref = parse_remote_registry(entry.pool_home)
        clone_dir = Path(entry.clone_path) if entry.clone_path else None
        if clone_dir is None or not (clone_dir / ".git").is_dir():
            clone_hint = (
                f" at {clone_dir}" if clone_dir
                else " (no clone_path configured)"
            )
            return PoolResult(
                pool_path=Path(),
                valid=False,
                errors=[
                    f"registry '{actual_reg_id}' has no local clone"
                    f"{clone_hint}. "
                    f"Run 'alph registry clone {actual_reg_id}' first.",
                ],
                config_path=global_config_dir / "config.yaml",
            )
        # Use clone_path + subpath as the real registry home.
        registry_home = clone_dir / ref.subpath if ref.subpath else clone_dir

    pool_path = registry_home / name

    # Check for duplicate: pool already in config.
    global_config_path = global_config_dir / "config.yaml"
    if global_config_path.exists():
        existing_data = yaml.safe_load(global_config_path.read_text()) or {}
        if isinstance(existing_data, dict):
            existing_regs = existing_data.get("registries", {})
            if isinstance(existing_regs, dict):
                reg_entry_raw = existing_regs.get(actual_reg_id, {})
                if isinstance(reg_entry_raw, dict):
                    existing_pools = reg_entry_raw.get("pools", {})
                    if isinstance(existing_pools, dict) and name in existing_pools:
                        return PoolResult(
                            pool_path=pool_path,
                            valid=False,
                            errors=[
                                f"pool '{name}' already exists in registry "
                                f"'{actual_reg_id}'",
                            ],
                            config_path=global_config_path,
                        )

    # Check for duplicate: pool directory already on disk.
    if pool_path.is_dir() and any(pool_path.iterdir()):
        return PoolResult(
            pool_path=pool_path,
            valid=False,
            errors=[
                f"pool directory already exists: {pool_path}",
            ],
            config_path=global_config_dir / "config.yaml",
        )

    for subdir in ("snapshots", "live"):
        (pool_path / subdir).mkdir(parents=True, exist_ok=True)

    # Update the registry entry in the GLOBAL config to add pool info.
    global_config_path = global_config_dir / "config.yaml"
    global_data: dict[str, object] = {}
    if global_config_path.exists():
        loaded = yaml.safe_load(global_config_path.read_text()) or {}
        if isinstance(loaded, dict):
            global_data = loaded

    global_registries = global_data.get("registries", {})
    if not isinstance(global_registries, dict):
        global_registries = {}

    reg_entry = global_registries.get(actual_reg_id, {})
    if isinstance(reg_entry, str):
        reg_entry = {"pool_home": reg_entry}
    if not isinstance(reg_entry, dict):
        reg_entry = {}

    pools = reg_entry.get("pools", {})
    if not isinstance(pools, dict):
        pools = {}
    pools[name] = {"context": context, "type": pool_type}
    reg_entry["pools"] = pools
    global_registries[actual_reg_id] = reg_entry
    global_data["registries"] = global_registries

    # Set default_pool if this is the default registry and none is set.
    default_reg = str(global_data.get("default_registry", ""))
    if default_reg == actual_reg_id and not global_data.get("default_pool"):
        global_data["default_pool"] = name

    global_config_path.write_text(
        yaml.dump(global_data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    )
    logger.debug("init_pool: added pool %r to registry %r in %s", name, actual_reg_id, global_config_path)

    validation = validate_registry(global_data)
    return PoolResult(
        pool_path=pool_path,
        valid=validation.valid,
        errors=validation.errors,
        config_path=global_config_path,
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
    for subdir in ("snapshots", "live"):
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
    Markdown file with YAML frontmatter to ``snapshots/`` (snapshot nodes) or
    ``live/`` (live nodes).

    Args:
        pool_path: Root directory of the pool.
        source: Originating system (e.g. ``"cli"``).
        node_type: ``"snapshot"`` or ``"live"``.
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
    if node_type == "snap":
        node_type = "snapshot"
    node_id = generate_id(source=source, context=context)
    logger.debug("create_node: id=%s type=%s pool=%s", node_id, node_type, pool_path)

    existing = check_idempotency(pool_path, node_id)
    if existing is not None:
        logger.debug("create_node: duplicate detected, created by %r", existing.creator)
        subdir = "snapshots" if node_type == "snapshot" else "live"
        path = pool_path / subdir / f"{node_id}.md"
        return NodeResult(
            node_id=node_id,
            path=path,
            duplicate=True,
            existing_creator=existing.creator,
        )

    subdir = "snapshots" if node_type == "snapshot" else "live"
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
    for exact filtering — e.g. ``{"archived"}`` for only archived nodes, or
    ``{"active", "archived", "suppressed"}`` for everything.

    Args:
        pool_path: Root directory of the pool.
        include_statuses: Exact set of status values to include. None means active only.

    Returns:
        List of NodeSummary, one per matching node file.
    """
    allowed = include_statuses if include_statuses is not None else {_DEFAULT_STATUS}
    summaries: list[NodeSummary] = []
    for subdir in ("snapshots", "live"):
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
    for subdir in ("snapshots", "live"):
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
