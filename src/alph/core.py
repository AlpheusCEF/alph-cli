"""AlpheusCEF core logic. No framework dependencies. All business logic lives here."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

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
    """Merged configuration from global, pool, and CLI override layers."""

    creator: str = ""
    auto_commit: bool = False
    default_registry: str = ""
    default_pool: str = ""


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


@dataclass(frozen=True)
class PoolResult:
    """Result of init_pool."""

    pool_path: Path
    valid: bool
    errors: list[str] = field(default_factory=list)


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
    pool_path: Path,
    overrides: dict[str, object] | None = None,
) -> AlphConfig:
    """Load and merge configuration from global, pool, and CLI layers.

    Priority (highest wins): CLI overrides > pool config > global config > defaults.

    Args:
        global_config_dir: Directory containing the global ``config.yaml``
            (typically ``~/.config/alph/``).
        pool_path: Root of the pool; pool config lives at
            ``<pool_path>/.alph/config.yaml``.
        overrides: Per-invocation overrides (from CLI flags).

    Returns:
        Merged AlphConfig.
    """
    merged: dict[str, object] = {}

    for config_path in (
        global_config_dir / "config.yaml",
        pool_path / ".alph" / "config.yaml",
    ):
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text()) or {}
            if isinstance(data, dict):
                merged.update(data)

    if overrides:
        merged.update(overrides)

    return AlphConfig(
        creator=str(merged.get("creator", "")),
        auto_commit=bool(merged.get("auto_commit", False)),
        default_registry=str(merged.get("default_registry", "")),
        default_pool=str(merged.get("default_pool", "")),
    )


# ---------------------------------------------------------------------------
# Registry and pool initialisation
# ---------------------------------------------------------------------------


def init_registry(
    *,
    path: Path,
    registry_id: str,
    context: str,
    name: str = "",
) -> RegistryResult:
    """Create a registry directory and write its config.yaml.

    Args:
        path: Directory to create as the registry root.
        registry_id: Machine identifier for the registry.
        context: Human/LLM-readable description.
        name: Optional human-readable name.

    Returns:
        RegistryResult with config_path and validation outcome.
    """
    path.mkdir(parents=True, exist_ok=True)

    registry_entry: dict[str, object] = {"context": context}
    if name:
        registry_entry["name"] = name

    config: dict[str, object] = {
        "registries": {registry_id: registry_entry},
        "pools": {},
    }
    config_path = path / "config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True))

    validation = validate_registry(config)
    return RegistryResult(
        config_path=config_path,
        valid=validation.valid,
        errors=validation.errors,
    )


def init_pool(
    *,
    registry_path: Path,
    name: str,
    context: str,
    layout: str = "subdirectory",
) -> PoolResult:
    """Create a pool and register it in the registry config.

    Args:
        registry_path: Root directory of the parent registry.
        name: Pool name (machine identifier, used as directory name).
        context: Human/LLM-readable description.
        layout: ``"subdirectory"`` (default) or ``"repo"``.

    Returns:
        PoolResult with pool_path and validation outcome.
    """
    pool_path = registry_path / name
    for subdir in ("snapshots", "pointers", ".alph"):
        (pool_path / subdir).mkdir(parents=True, exist_ok=True)

    # Update registry config to include this pool
    config_path = registry_path / "config.yaml"
    config: dict[str, object] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text()) or {}
        if isinstance(loaded, dict):
            config = loaded

    pools = config.get("pools", {})
    if not isinstance(pools, dict):
        pools = {}
    pools[name] = {"context": context, "layout": layout, "path": f"./{name}"}
    config["pools"] = pools
    config_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True))

    validation = validate_registry(config)
    return PoolResult(
        pool_path=pool_path,
        valid=validation.valid,
        errors=validation.errors,
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
    tags: list[str] | None = None,
    related_to: list[str] | None = None,
    meta: dict[str, object] | None = None,
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
    resolved_timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    node_id = generate_id(timestamp=resolved_timestamp, source=source, context=context)

    existing = check_idempotency(pool_path, node_id)
    if existing is not None:
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
    return NodeResult(node_id=node_id, path=path)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def list_nodes(pool_path: Path) -> list[NodeSummary]:
    """List all nodes in a pool as lightweight summaries.

    Scans ``snapshots/`` and ``pointers/`` for Markdown files with valid
    frontmatter, sorted by timestamp ascending.

    Args:
        pool_path: Root directory of the pool.

    Returns:
        List of NodeSummary, one per valid node file.
    """
    summaries: list[NodeSummary] = []
    for subdir in ("snapshots", "pointers"):
        directory = pool_path / subdir
        if not directory.exists():
            continue
        for node_file in sorted(directory.glob("*.md")):
            frontmatter = extract_frontmatter(node_file.read_text())
            if not frontmatter:
                continue
            summaries.append(
                NodeSummary(
                    node_id=str(frontmatter.get("id", "")),
                    context=str(frontmatter.get("context", "")),
                    node_type=str(frontmatter.get("node_type", "")),
                    timestamp=str(frontmatter.get("timestamp", "")),
                    source=str(frontmatter.get("source", "")),
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
                tags=list(tags) if isinstance(tags, list) else [],
                related_to=list(related_to) if isinstance(related_to, list) else [],
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
