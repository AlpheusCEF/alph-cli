"""Thin Typer wrapper exposing core.py as the `alph` CLI."""

import csv
import importlib.metadata
import io
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from alph.core import (
    AlphConfig,
    RegistryEntry,
    check_git_state,
    collect_registries,
    create_node,
    effective_mode,
    extract_frontmatter,
    init_pool,
    init_registry,
    is_remote_registry,
    list_config_paths,
    list_nodes,
    list_pools,
    load_config,
    parse_remote_registry,
    resolve_default_pool,
    resolve_pool_name,
    show_node,
    validate_config_keys,
    validate_node,
)
from alph.remote import (
    clone_remote_registry,
    default_clone_dir,
    fetch_remote_pools_cached,
    provider_for_url,
    pull_remote_registry,
    push_remote_registry,
    resolve_pool_readonly,
)

_help_settings = {"help_option_names": ["-h", "--help"]}
app = typer.Typer(name="alph", help="Alpheus Context Engine Framework.\n\nRun 'alph examples' for structured usage walkthroughs.", context_settings=_help_settings)
registry_app = typer.Typer(help="Registry commands.", invoke_without_command=True, context_settings=_help_settings)
pool_app = typer.Typer(help="Pool commands.", invoke_without_command=True, context_settings=_help_settings)
config_app = typer.Typer(help="Config file commands.", invoke_without_command=True, context_settings=_help_settings)
app.add_typer(registry_app, name="registry")
app.add_typer(registry_app, name="reg", hidden=True)
app.add_typer(pool_app, name="pool")
app.add_typer(config_app, name="config")

def _console_width() -> int:
    try:
        return min(200, os.get_terminal_size().columns)
    except (ValueError, OSError):
        return 120


console = Console(width=_console_width())

_CLI_VERSION = importlib.metadata.version("alph-cli")
_CLI_SOURCE = f"alph-cli/v{_CLI_VERSION}"

_verbose: bool = False
_registry_override: str | None = None
_branch_override: str | None = None

_VERBOSE_OPT = typer.Option(False, "--verbose", "-v", help="Enable debug logging.")


def _apply_verbose(verbose: bool) -> None:
    """Enable DEBUG logging and set _verbose flag. No-op if verbose=False."""
    global _verbose
    if verbose:
        _verbose = True
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
        logging.getLogger().setLevel(logging.DEBUG)


def _version_callback(value: bool) -> None:
    if value:
        version = importlib.metadata.version("alph-cli")
        typer.echo(f"alph {version}")
        raise typer.Exit()


def _complete_registry_id(incomplete: str) -> list[str]:
    """Return registry IDs (+ 'all') matching the incomplete prefix."""
    try:
        cfg = _load_cli_config()
    except Exception:
        return []
    candidates = ["all", *cfg.registries.keys()]
    return [c for c in candidates if c.startswith(incomplete)]


def _effective_completion_remote(entry: RegistryEntry, cfg: AlphConfig) -> bool:
    """Return whether remote completion is enabled for this registry entry.

    Per-registry setting takes priority; None means inherit the global default.
    """
    if entry.completion_remote is not None:
        return entry.completion_remote
    return cfg.completion_remote


def _local_pool_home_for_entry(entry: RegistryEntry) -> Path | None:
    """Return the local directory to scan for pool names, or None if unavailable.

    - Local registries: pool_home directly.
    - RW remote with an existing clone: clone_path / subpath.
    - RO remote (or RW without a clone): None (no local scan possible).
    """
    if not is_remote_registry(entry.pool_home):
        return Path(entry.pool_home)
    if effective_mode(entry) == "rw" and entry.clone_path:
        clone = Path(entry.clone_path)
        if clone.is_dir():
            ref = parse_remote_registry(entry.pool_home)
            return clone / ref.subpath if ref.subpath else clone
    return None


def _complete_pool(incomplete: str) -> list[str]:
    """Return pool names matching the incomplete prefix.

    - Local registries and RW remote clones: scanned from disk (fast, no network).
    - RO remote registries: queried via the forge API when ``completion_remote``
      is enabled (global or per-registry), with results cached for
      ``completion_cache_ttl`` seconds (default 60).  Off by default to avoid
      unexpected network calls during tab completion.

    Any error (config, I/O, network) is silently swallowed so a failed
    completion does not interrupt the user's shell.
    """
    try:
        cfg = _load_cli_config()
    except Exception:
        return []

    names: list[str] = []
    for entry in cfg.registries.values():
        pool_home = _local_pool_home_for_entry(entry)
        if pool_home is not None:
            # Local path or RW clone — scan disk.
            if not pool_home.is_dir():
                continue
            for candidate in sorted(pool_home.iterdir()):
                if not candidate.is_dir():
                    continue
                if (candidate / "snapshots").is_dir() or (candidate / "live").is_dir():
                    names.append(candidate.name)
        elif _effective_completion_remote(entry, cfg):
            # RO remote with completion enabled — fetch via API (cached).
            try:
                ref = parse_remote_registry(entry.pool_home)
                prov = provider_for_url(ref.remote_url)
                remote_pools = fetch_remote_pools_cached(
                    prov,
                    ref.subpath,
                    cache_key=entry.pool_home,
                    ttl=cfg.completion_cache_ttl,
                )
                names.extend(remote_pools)
            except Exception:
                pass  # Network or auth error — skip this registry silently.

    return [n for n in names if n.startswith(incomplete)]


@app.callback()
def _main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging.", is_eager=False),
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    registry: str | None = typer.Option(
        None, "--registry", "--reg", "-r",
        help="Override registry: ID, name, or remote git URL. "
             "Scopes pool resolution to this registry for this invocation.",
        autocompletion=_complete_registry_id,
    ),
    branch: str | None = typer.Option(
        None, "--branch",
        help="Override git branch for remote operations. "
             "Useful with ad-hoc --registry URLs that have no config entry.",
    ),
) -> None:
    """Alpheus Context Engine Framework."""
    global _registry_override, _branch_override
    _registry_override = registry
    _branch_override = branch
    if not logging.root.handlers:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    _apply_verbose(verbose)


def _global_config_dir() -> Path:
    """Return the global config directory, overridable via ALPH_CONFIG_DIR for tests."""
    override = os.environ.get("ALPH_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".config" / "alph"


def _load_cli_config(cwd: Path | None = None) -> AlphConfig:
    """Load merged config for a CLI invocation."""
    # Use os.getcwd() rather than Path.cwd() so macOS symlinks (/tmp → /private/tmp)
    # are not resolved — the user sees the path they typed, not the kernel path.
    try:
        effective_cwd = cwd or Path(os.getcwd())
    except FileNotFoundError:
        # Shell cwd was deleted; fall back to home so config walk doesn't crash.
        effective_cwd = Path.home()
    return load_config(
        global_config_dir=_global_config_dir(),
        cwd=effective_cwd,
    )


def _resolve_registry_id(registry_id: str | None, cfg: AlphConfig) -> str:
    """Resolve an optional registry ID, falling back to default_registry.

    Exits with an error if no ID was provided and no default is configured.
    """
    if registry_id:
        return registry_id
    if cfg.default_registry:
        return cfg.default_registry
    console.print(
        "[red]error:[/red] no registry specified and no default_registry set.\n"
        "  pass a REGISTRY_ID argument or set default_registry in config."
    )
    raise typer.Exit(code=1)


def _require_pool(pool_flag: str | None, cfg: AlphConfig) -> str:
    """Resolve pool to a string (path or remote URL). Exits on error.

    Returns the raw string so callers can detect remote URLs. The flag
    value is resolved in order:
    1. Remote URL (git@/https://) — returned as-is.
    2. Absolute path or existing relative path — returned as-is.
    3. Pool name found in a registry — resolved to registry_home/name string.
    4. Falls back to default_registry/default_pool from config.

    If ``--registry`` global option was used, pool resolution is scoped
    to that registry (by ID/name) or constructed from that URL.
    """
    reg_override = _registry_override

    # If --registry is a remote URL, combine with pool name.
    if reg_override and is_remote_registry(reg_override):
        ref = parse_remote_registry(reg_override)
        pool_name = pool_flag or cfg.default_pool
        if not pool_name:
            console.print(
                "[red]error:[/red] --pool required when --registry "
                "is a remote URL."
            )
            raise typer.Exit(code=1)
        pool_subpath = (
            f"{ref.subpath}/{pool_name}" if ref.subpath else pool_name
        )
        return f"{ref.remote_url}:/{pool_subpath}"

    # If --registry is an ID/name, scope resolution to that registry.
    if reg_override:
        from alph.core import find_registry_config

        found = find_registry_config(reg_override, cfg=cfg)
        if found is None:
            console.print(
                f"[red]error:[/red] registry '{reg_override}' not found."
            )
            raise typer.Exit(code=1)
        reg_id, _ = found
        entry = cfg.registries[reg_id]
        pool_name = pool_flag or cfg.default_pool
        if not pool_name:
            console.print(
                "[red]error:[/red] --pool required when --registry is set."
            )
            raise typer.Exit(code=1)
        if is_remote_registry(entry.pool_home):
            ref = parse_remote_registry(entry.pool_home)
            pool_subpath = (
                f"{ref.subpath}/{pool_name}" if ref.subpath
                else pool_name
            )
            return f"{ref.remote_url}:/{pool_subpath}"
        return str(Path(entry.pool_home) / pool_name)

    if pool_flag is not None:
        if is_remote_registry(pool_flag):
            return pool_flag
        p = Path(pool_flag)
        if p.is_absolute() or p.exists():
            return str(p)
        by_name = resolve_pool_name(pool_flag, cfg)
        if by_name is not None:
            return str(by_name)
        console.print(
            f"[red]error:[/red] --pool '{pool_flag}' is not a path and was not found "
            "as a pool name in any known registry"
        )
        raise typer.Exit(code=1)

    # No flag — check default pool. If the default registry is remote,
    # we need to return the full remote URL + pool name.
    if cfg.default_registry and cfg.default_pool:
        default_entry = cfg.registries.get(cfg.default_registry)
        if default_entry and is_remote_registry(default_entry.pool_home):
            ref = parse_remote_registry(default_entry.pool_home)
            pool_subpath = (
                f"{ref.subpath}/{cfg.default_pool}" if ref.subpath
                else cfg.default_pool
            )
            return f"{ref.remote_url}:/{pool_subpath}"

    resolved = resolve_default_pool(cfg)
    if resolved is None:
        console.print(
            "[red]error:[/red] --pool required, or set default_registry + "
            "default_pool and registries in ~/.config/alph/config.yaml"
        )
        raise typer.Exit(code=1)
    return str(resolved)


def _find_entry_for_pool(pool_str: str, cfg: AlphConfig) -> RegistryEntry | None:
    """Find the RegistryEntry that owns a pool, if any.

    When ``--registry`` was used, returns that specific registry's entry
    so the user's intent is honoured even when multiple registries share
    the same URL.  Otherwise, when multiple remote registries share the
    same URL, prefers the RW entry so write operations succeed.
    """
    # Honour explicit --registry override.
    reg_override = _registry_override
    if reg_override:
        if is_remote_registry(reg_override):
            # Ad-hoc URL — no config entry; force ephemeral RO.
            return None
        from alph.core import find_registry_config

        found = find_registry_config(reg_override, cfg=cfg)
        if found is not None:
            reg_id, _ = found
            return cfg.registries[reg_id]

    if is_remote_registry(pool_str):
        ref_pool = parse_remote_registry(pool_str)
        match: RegistryEntry | None = None
        for entry in cfg.registries.values():
            if not is_remote_registry(entry.pool_home):
                continue
            ref_entry = parse_remote_registry(entry.pool_home)
            if ref_entry.remote_url != ref_pool.remote_url:
                continue
            if match is None or (
                effective_mode(entry) == "rw" and effective_mode(match) != "rw"
            ):
                match = entry
        return match

    for entry in cfg.registries.values():
        if is_remote_registry(entry.pool_home):
            continue
        pool_home = Path(entry.pool_home)
        try:
            if Path(pool_str).is_relative_to(pool_home):
                return entry
        except (ValueError, TypeError):
            pass
    return None


@contextmanager
def _pool_context(
    pool_str: str, cfg: AlphConfig, *, writable: bool = False,
) -> Iterator[Path]:
    """Yield a local Path for pool operations.

    For local pools, yields the path directly. For remote RO pools,
    fetches files to a tmpdir via the provider API and yields that path.
    For remote RW pools, clones locally and yields the pool subpath
    within the clone.
    """
    if not is_remote_registry(pool_str):
        yield Path(pool_str)
        return

    entry = _find_entry_for_pool(pool_str, cfg)
    mode = effective_mode(entry) if entry else "ro"

    if writable and mode == "ro":
        console.print(
            "[red]error:[/red] registry is read-only. "
            "Set mode: rw in config to enable writes."
        )
        raise typer.Exit(code=1)

    ref = parse_remote_registry(pool_str)

    if mode == "rw":
        # RW path — use local clone.
        clone_dir = (
            Path(entry.clone_path) if entry and entry.clone_path
            else default_clone_dir(ref.remote_url)
        )
        rw_branch = _branch_override or (entry.branch if entry and entry.branch else "")
        ssh_cmd = entry.ssh_command if entry else ""
        clone_remote_registry(ref.remote_url, clone_dir, branch=rw_branch, ssh_command=ssh_cmd)
        if entry and entry.auto_pull and (clone_dir / ".git").is_dir():
            try:
                pull_remote_registry(clone_dir, ssh_command=ssh_cmd)
            except RuntimeError as exc:
                console.print(f"[yellow]warning:[/yellow] auto-pull failed: {exc}")
        pool_path = clone_dir / ref.subpath if ref.subpath else clone_dir
        yield pool_path
        return

    # RO path — fetch via provider API.
    branch = _branch_override or (entry.branch if entry and entry.branch else "HEAD")
    provider = provider_for_url(ref.remote_url)
    with resolve_pool_readonly(provider, ref.subpath, ref=branch) as pool_path:
        yield pool_path


def _pull_if_requested(
    pool_str: str, cfg: AlphConfig, *, pull: bool,
) -> None:
    """Pull latest changes for an RW clone if --pull was passed."""
    if not pull:
        return
    if not is_remote_registry(pool_str):
        return
    entry = _find_entry_for_pool(pool_str, cfg)
    # auto_pull already handled in _pool_context — skip to avoid double pull.
    if entry and entry.auto_pull:
        return
    mode = effective_mode(entry) if entry else "ro"
    if mode != "rw":
        # RO mode — pull is a no-op (fresh API fetch happens automatically).
        return
    ref = parse_remote_registry(pool_str)
    clone_dir = (
        Path(entry.clone_path) if entry and entry.clone_path
        else default_clone_dir(ref.remote_url)
    )
    ssh_cmd = entry.ssh_command if entry else ""
    try:
        pull_remote_registry(clone_dir, ssh_command=ssh_cmd)
    except FileNotFoundError:
        console.print(
            f"[yellow]warning:[/yellow] no clone found at {clone_dir}. "
            "Skipping pull."
        )
    except RuntimeError as exc:
        console.print(f"[yellow]warning:[/yellow] pull failed: {exc}")


def _registry_id_for_entry(entry: RegistryEntry, cfg: AlphConfig) -> str | None:
    """Return the registry ID for a given entry, or None if not found."""
    for reg_id, reg_entry in cfg.registries.items():
        if reg_entry is entry:
            return reg_id
    return None


def _auto_push_if_configured(
    pool_str: str, cfg: AlphConfig,
) -> None:
    """Push after write if auto_push is set on the registry."""
    if not is_remote_registry(pool_str):
        return
    entry = _find_entry_for_pool(pool_str, cfg)
    if not entry or not entry.auto_push:
        return
    mode = effective_mode(entry)
    if mode != "rw":
        return
    ref = parse_remote_registry(pool_str)
    clone_dir = (
        Path(entry.clone_path) if entry.clone_path
        else default_clone_dir(ref.remote_url)
    )
    try:
        push_remote_registry(clone_dir, ssh_command=entry.ssh_command)
        console.print("[dim]auto-pushed to remote.[/dim]")
    except (FileNotFoundError, RuntimeError) as exc:
        reg_id = _registry_id_for_entry(entry, cfg)
        hint = f" Run 'alph registry push {reg_id}' to retry." if reg_id else ""
        console.print(f"[red]error:[/red] auto-push failed: {exc}{hint}")


def _require_creator(creator_flag: str | None, cfg: AlphConfig) -> str:
    """Resolve creator from flag or config. Exits with error if neither is set."""
    if creator_flag:
        return creator_flag
    if cfg.creator:
        return cfg.creator
    console.print("[red]error:[/red] --creator required, or set creator in ~/.config/alph/config.yaml")
    raise typer.Exit(code=1)


@registry_app.callback()
def _registry_default(ctx: typer.Context) -> None:
    """Registry commands. Defaults to 'list' when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(registry_list, cwd=None, verbose=False)


@registry_app.command("init")
def registry_init(
    pool_home: str = typer.Option(..., "--pool-home", help="Directory or git remote URL (e.g. git@github.com:org/repo.git:/subpath)."),
    registry_id: str = typer.Option(..., "--id", help="Machine identifier for the registry."),
    context: str = typer.Option(..., "--context", "-c", help="Human/LLM-readable description."),
    name: str = typer.Option("", "--name", help="Optional human-readable name."),
    mode: str = typer.Option("", "--mode", help="Access mode: 'ro' or 'rw'. Auto-detected if omitted (ro for remote, rw for local)."),
    clone_path: str = typer.Option("", "--clone-path", help="Local directory for RW clone (remote registries only)."),
    branch: str = typer.Option("", "--branch", help="Git branch for RO reads and RW clone checkout."),
    auto_push: bool | None = typer.Option(None, "--auto-push/--no-auto-push", help="Push after commit. Default: true for RW remote, false for local."),
    auto_pull: bool | None = typer.Option(None, "--auto-pull/--no-auto-pull", help="Pull before read. Default: true for RW remote, false for local."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Create a registry and register it in the global config.

    The registry definition is written into the global config
    (~/.config/alph/config.yaml). For local registries, the --pool-home
    directory is created on disk. For remote registries (git URLs), no
    directory is created.

    If no default registry is set yet, this one becomes the default.
    """
    _apply_verbose(verbose)
    cfg = _load_cli_config()
    result = init_registry(
        pool_home=Path(pool_home),
        registry_id=registry_id,
        context=context,
        name=name,
        mode=mode,
        clone_path=clone_path,
        branch=branch,
        auto_push=auto_push,
        auto_pull=auto_pull,
        global_config_dir=_global_config_dir(),
    )
    if not result.valid:
        for error in result.errors:
            console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(code=1)
    console.print(f"[green]registry created:[/green] {registry_id}")
    console.print(f"  pool home: {pool_home}")
    if mode:
        console.print(f"  mode: {mode}")
    if branch:
        console.print(f"  branch: {branch}")
    if clone_path:
        console.print(f"  clone path: {clone_path}")
    console.print(f"  config: {result.config_path}")
    if result.set_as_default:
        console.print("  [dim]set as default registry[/dim]")
    elif cfg.defaults_reminder:
        console.print("  [dim](not set as default — another default registry already exists)[/dim]")
        console.print(f"  [dim]to make it default: set default_registry: {registry_id} in ~/.config/alph/config.yaml[/dim]")


@registry_app.command("list")
def registry_list(
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for config lookup."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """List all registries known to alph from the config file tree."""
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)
    summaries = collect_registries(cfg=cfg)
    if not summaries:
        console.print("no registries found.")
        console.print("  run [bold]alph registry init[/bold] to create one.")
        return
    table = Table(show_header=True, header_style="bold", row_styles=["", "dim"])
    table.add_column("ID", style="dim", width=20)
    table.add_column("name", width=20)
    table.add_column("mode", width=6)
    table.add_column("context")
    table.add_column("pool home")
    for s in summaries:
        entry = cfg.registries.get(s.registry_id)
        mode = effective_mode(entry) if entry else "rw"
        table.add_row(
            s.registry_id, s.name, mode, s.context, str(s.home_path),
        )
    console.print(table)


def _check_single_registry(reg_id: str, cfg: AlphConfig) -> None:
    """Check reachability of a single registry. Raises typer.Exit on failure."""
    import subprocess as _subprocess

    entry = cfg.registries[reg_id]

    if not is_remote_registry(entry.pool_home):
        pool_home_path = Path(entry.pool_home)
        if pool_home_path.exists():
            console.print(
                f"[green]ok:[/green] {reg_id} is a local registry "
                f"at {entry.pool_home}"
            )
        else:
            console.print(
                f"[red]error:[/red] {reg_id} local path does not "
                f"exist: {entry.pool_home}"
            )
            raise typer.Exit(code=1)
        return

    ref = parse_remote_registry(entry.pool_home)
    try:
        result = _subprocess.run(
            ["git", "ls-remote", "--exit-code", ref.remote_url],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            console.print(
                f"[green]ok:[/green] {reg_id} remote is reachable "
                f"({ref.remote_url})"
            )
        else:
            console.print(
                f"[red]error:[/red] {reg_id} remote not reachable: "
                f"{result.stderr.strip()}"
            )
            raise typer.Exit(code=1)
    except _subprocess.TimeoutExpired:
        console.print(
            f"[red]error:[/red] {reg_id} remote timed out "
            f"({ref.remote_url})"
        )
        raise typer.Exit(code=1) from None
    except FileNotFoundError:
        console.print(
            "[red]error:[/red] git not found. "
            "Install git to check remote registries."
        )
        raise typer.Exit(code=1) from None


@registry_app.command("check")
def registry_check(
    registry_id: str | None = typer.Argument(
        None, help="Registry ID, name, or 'all'. Defaults to default_registry.",
        autocompletion=_complete_registry_id,
    ),
    cwd: Path | None = typer.Option(
        None, "--cwd", hidden=True,
        help="Working directory for config lookup.",
    ),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Verify a remote registry is reachable.

    Runs git ls-remote against the registry's pool_home URL to confirm
    the remote is accessible with current credentials.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)
    resolved_id = _resolve_registry_id(registry_id, cfg)

    from alph.core import find_registry_config

    if resolved_id == "all":
        if not cfg.registries:
            console.print("no registries found.")
            return
        any_failed = False
        for rid in cfg.registries:
            try:
                _check_single_registry(rid, cfg)
            except typer.Exit:
                any_failed = True
        if any_failed:
            raise typer.Exit(code=1)
        return

    found = find_registry_config(resolved_id, cfg=cfg)
    if found is None:
        known = ", ".join(cfg.registries.keys()) or "(none)"
        console.print(
            f"[red]error:[/red] {resolved_id} not found. "
            f"Known registries: {known}"
        )
        raise typer.Exit(code=1)

    reg_id, _ = found
    _check_single_registry(reg_id, cfg)


@registry_app.command("clone")
def registry_clone(
    registry_id: str | None = typer.Argument(
        None, help="Registry ID or name to clone. Defaults to default_registry.",
        autocompletion=_complete_registry_id,
    ),
    clone_path: Path | None = typer.Option(
        None, "--clone-path",
        help="Local directory to clone into. Default: ~/.cache/alph/clones/<hash>.",
    ),
    cwd: Path | None = typer.Option(
        None, "--cwd", hidden=True,
        help="Working directory for config lookup.",
    ),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Clone a remote registry locally for RW access.

    Creates a shallow git clone of the registry's remote URL. If the
    clone already exists, reports it with a distinct message.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)
    resolved_id = _resolve_registry_id(registry_id, cfg)

    from alph.core import find_registry_config

    found = find_registry_config(resolved_id, cfg=cfg)
    if found is None:
        known = ", ".join(cfg.registries.keys()) or "(none)"
        console.print(
            f"[red]error:[/red] {resolved_id} not found. "
            f"Known registries: {known}"
        )
        raise typer.Exit(code=1)

    reg_id, _ = found
    entry = cfg.registries[reg_id]

    if not is_remote_registry(entry.pool_home):
        console.print(
            f"[red]error:[/red] {reg_id} is a local registry — "
            "clone is only for remote registries."
        )
        raise typer.Exit(code=1)

    ref = parse_remote_registry(entry.pool_home)
    target = (
        clone_path
        or (Path(entry.clone_path) if entry.clone_path else None)
        or default_clone_dir(ref.remote_url)
    )
    try:
        created = clone_remote_registry(
            ref.remote_url, target, branch=entry.branch, ssh_command=entry.ssh_command,
        )
    except RuntimeError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from None
    if created:
        msg = f"[green]cloned:[/green] {reg_id} -> {target}"
        if entry.branch:
            msg += f" (branch: {entry.branch})"
        console.print(msg)
    else:
        console.print(f"[green]ok:[/green] {reg_id} already cloned at {target}")


@registry_app.command("pull")
def registry_pull(
    registry_id: str | None = typer.Argument(
        None, help="Registry ID or name to pull. Defaults to default_registry.",
        autocompletion=_complete_registry_id,
    ),
    cwd: Path | None = typer.Option(
        None, "--cwd", hidden=True,
        help="Working directory for config lookup.",
    ),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Pull latest changes for a cloned remote registry.

    Runs git pull --rebase in the local clone directory.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)
    resolved_id = _resolve_registry_id(registry_id, cfg)

    from alph.core import find_registry_config

    found = find_registry_config(resolved_id, cfg=cfg)
    if found is None:
        known = ", ".join(cfg.registries.keys()) or "(none)"
        console.print(
            f"[red]error:[/red] {resolved_id} not found. "
            f"Known registries: {known}"
        )
        raise typer.Exit(code=1)

    reg_id, _ = found
    entry = cfg.registries[reg_id]

    if not is_remote_registry(entry.pool_home):
        console.print(
            f"[red]error:[/red] {reg_id} is a local registry — "
            "pull is only for remote registries."
        )
        raise typer.Exit(code=1)

    ref = parse_remote_registry(entry.pool_home)
    clone_dir = (
        Path(entry.clone_path) if entry.clone_path
        else default_clone_dir(ref.remote_url)
    )
    try:
        pull_remote_registry(clone_dir, ssh_command=entry.ssh_command)
    except FileNotFoundError:
        console.print(
            f"[red]error:[/red] no clone found at {clone_dir}. "
            f"Run 'alph registry clone {reg_id}' first."
        )
        raise typer.Exit(code=1) from None
    except RuntimeError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from None
    console.print(f"[green]pulled:[/green] {reg_id} ({clone_dir})")


@registry_app.command("push")
def registry_push(
    registry_id: str | None = typer.Argument(
        None, help="Registry ID or name to push. Defaults to default_registry.",
        autocompletion=_complete_registry_id,
    ),
    cwd: Path | None = typer.Option(
        None, "--cwd", hidden=True,
        help="Working directory for config lookup.",
    ),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Push local commits in a cloned remote registry to the remote.

    Use this to recover after a failed auto-push, or to push explicitly
    when auto_push is disabled.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)
    resolved_id = _resolve_registry_id(registry_id, cfg)

    from alph.core import find_registry_config

    found = find_registry_config(resolved_id, cfg=cfg)
    if found is None:
        known = ", ".join(cfg.registries.keys()) or "(none)"
        console.print(
            f"[red]error:[/red] {resolved_id} not found. "
            f"Known registries: {known}"
        )
        raise typer.Exit(code=1)

    reg_id, _ = found
    entry = cfg.registries[reg_id]

    if not is_remote_registry(entry.pool_home):
        console.print(
            f"[red]error:[/red] {reg_id} is a local registry — "
            "push is only for remote registries."
        )
        raise typer.Exit(code=1)

    mode = effective_mode(entry)
    if mode != "rw":
        console.print(
            f"[red]error:[/red] {reg_id} is read-only — push requires mode: rw."
        )
        raise typer.Exit(code=1)

    ref = parse_remote_registry(entry.pool_home)
    clone_dir = (
        Path(entry.clone_path) if entry.clone_path
        else default_clone_dir(ref.remote_url)
    )
    try:
        push_remote_registry(clone_dir, ssh_command=entry.ssh_command)
    except FileNotFoundError:
        console.print(
            f"[red]error:[/red] no clone found at {clone_dir}. "
            f"Run 'alph registry clone {reg_id}' first."
        )
        raise typer.Exit(code=1) from None
    except RuntimeError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from None
    console.print(f"[green]pushed:[/green] {reg_id} ({clone_dir})")


def _status_single_registry(reg_id: str, cfg: AlphConfig) -> None:
    """Print status for one registry by ID."""
    import subprocess

    entry = cfg.registries[reg_id]
    mode = effective_mode(entry)

    lines: list[str] = [f"registry:    {reg_id}"]
    lines.append(f"mode:        {mode}")

    if is_remote_registry(entry.pool_home):
        ref = parse_remote_registry(entry.pool_home)
        lines.append(f"remote:      {ref.remote_url}")
        if ref.subpath:
            lines.append(f"subpath:     {ref.subpath}")
        if entry.branch:
            lines.append(f"branch:      {entry.branch}")

        clone_dir = (
            Path(entry.clone_path) if entry.clone_path
            else default_clone_dir(ref.remote_url)
        )
        lines.append(f"clone_path:  {clone_dir}")

        if (clone_dir / ".git").is_dir():
            git_status = subprocess.run(
                ["git", "-C", str(clone_dir), "status", "--porcelain"],
                capture_output=True, text=True, timeout=10,
            )
            dirty = bool(git_status.stdout.strip()) if git_status.returncode == 0 else False
            state = "cloned (dirty)" if dirty else "cloned (clean)"
            lines.append(f"clone_state: {state}")
            git_unpushed = subprocess.run(
                ["git", "-C", str(clone_dir), "log", "@{u}..HEAD", "--oneline"],
                capture_output=True, text=True, timeout=10,
            )
            if git_unpushed.returncode == 0:
                unpushed = sum(1 for line in git_unpushed.stdout.splitlines() if line.strip())
                lines.append(f"unpushed:    {unpushed}")
        else:
            lines.append("clone_state: not cloned")

        lines.append(f"auto_pull:   {str(entry.auto_pull).lower()}")
        lines.append(f"auto_push:   {str(entry.auto_push).lower()}")
    else:
        pool_home = Path(entry.pool_home)
        lines.append(f"path:        {pool_home}")
        exists = pool_home.is_dir()
        lines.append(f"exists:      {str(exists).lower()}")

    console.print("\n".join(lines))


@registry_app.command("status")
def registry_status(
    registry_id: str | None = typer.Argument(
        None, help="Registry ID or name to inspect. Defaults to default_registry. Use 'all' to show every registry.",
        autocompletion=_complete_registry_id,
    ),
    cwd: Path | None = typer.Option(
        None, "--cwd", hidden=True,
        help="Working directory for config lookup.",
    ),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Show status of a registry — mode, clone state, branch, auto_push, and path details.

    Pass 'all' to iterate every configured registry.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)
    resolved_id = _resolve_registry_id(registry_id, cfg)

    if resolved_id == "all":
        if not cfg.registries:
            console.print("no registries found.")
            return
        for i, rid in enumerate(cfg.registries):
            if i > 0:
                console.print("")
            _status_single_registry(rid, cfg)
        return

    from alph.core import find_registry_config

    found = find_registry_config(resolved_id, cfg=cfg)
    if found is None:
        known = ", ".join(cfg.registries.keys()) or "(none)"
        console.print(
            f"[red]error:[/red] {resolved_id} not found. "
            f"Known registries: {known}"
        )
        raise typer.Exit(code=1)

    reg_id, _ = found
    _status_single_registry(reg_id, cfg)


@pool_app.callback()
def _pool_default(ctx: typer.Context) -> None:
    """Pool commands. Defaults to 'list' when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(pool_list, registry=None, cwd=None, verbose=False)


@pool_app.command("init")
def pool_init(
    registry: str | None = typer.Option(None, "--registry", "--reg", "-r", help="Registry ID or name. Defaults to default_registry from config.", autocompletion=_complete_registry_id),
    name: str = typer.Option(..., "--name", help="Pool name (machine identifier)."),
    context: str = typer.Option(..., "--context", "-c", help="Human/LLM-readable description."),
    pool_type: str = typer.Option("subdir", "--type", help="Pool type: 'subdir' (pool is a subdirectory of the registry home) or 'repo' (standalone git repository)."),
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for registry lookup."),
    bootstrap: bool = typer.Option(False, "--bootstrap", hidden=True, help="Create registry if not found."),
    registry_context: str = typer.Option("", "--registry-context", hidden=True, help="Context for bootstrapped registry."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Create a pool inside a registry, register it, and validate it.

    The registry is located by ID or name, walking up from the current
    directory (or --cwd) and checking the global config. Use
    'alph registry list' to see which registries are known.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)

    registry_id = registry
    if registry_id is None:
        registry_id = cfg.default_registry or None
        if not registry_id:
            console.print(
                "[red]error:[/red] --registry required, or set default_registry "
                "in ~/.config/alph/config.yaml"
            )
            raise typer.Exit(code=1)

    result = init_pool(
        registry_id=registry_id,
        name=name,
        context=context,
        pool_type=pool_type,
        cwd=resolved_cwd,
        global_config_dir=_global_config_dir(),
        bootstrap=bootstrap,
        registry_context=registry_context,
    )
    if not result.valid:
        for error in result.errors:
            console.print(f"[red]error:[/red] {error}")
        # If error is about registry not found, show what we know about.
        if any("not found" in e for e in result.errors):
            summaries = collect_registries(cfg=cfg)
            if summaries:
                console.print("\nknown registries (from 'alph registry list'):")
                for s in summaries:
                    label = f"  {s.registry_id}"
                    if s.name:
                        label += f" ({s.name})"
                    console.print(f"{label}  —  {s.context}")
            else:
                console.print("  no registries found. Run [bold]alph registry init[/bold] first.")
        raise typer.Exit(code=1)
    console.print(f"[green]pool created:[/green] {name}")
    console.print(f"  registry: {registry_id}")
    console.print(f"  path:     {result.pool_path}")
    console.print(f"  config:   {result.config_path}")


@pool_app.command("list")
def pool_list(
    registry: str | None = typer.Option(None, "--registry", "--reg", "-r", help="Registry ID or name. Defaults to default_registry from config.", autocompletion=_complete_registry_id),
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for config lookup."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """List pools in a registry (configured and discovered on disk)."""
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)

    registry_id = registry or cfg.default_registry or None
    if not registry_id:
        console.print(
            "[red]error:[/red] --registry required, or set default_registry "
            "in ~/.config/alph/config.yaml"
        )
        raise typer.Exit(code=1)

    summaries = list_pools(registry_id, cfg=cfg)
    if summaries is None:
        console.print(f"[red]error:[/red] registry not found: {registry_id}")
        known = collect_registries(cfg=cfg)
        if known:
            console.print("known registries:")
            for reg in known:
                console.print(f"  {reg.registry_id}" + (f" ({reg.name})" if reg.name else ""))
        raise typer.Exit(code=1)

    if not summaries:
        console.print(f"no pools found in registry: {registry_id}")
        console.print("  run [bold]alph pool init[/bold] to create one.")
        return

    table = Table(show_header=True, header_style="bold", row_styles=["", "dim"])
    table.add_column("registry", width=20)
    table.add_column("name", width=20)
    table.add_column("type", width=8)
    if _verbose:
        table.add_column("source", width=12)
    table.add_column("context")
    table.add_column("path")
    for s in summaries:
        row: list[str] = [registry_id, s.name, s.pool_type]
        if _verbose:
            row.append(s.source)
        row.extend([s.context, str(s.path)])
        table.add_row(*row)
    console.print(table)


@app.command("add")
def cmd_add(
    context: str = typer.Option(..., "-c", "--context", help="Context description for this node."),
    pool: str | None = typer.Option(None, "--pool", "-p", help="Pool path or name. Defaults to default_pool from config.", autocompletion=_complete_pool),
    creator: str | None = typer.Option(None, "--creator", help="Creator email address. Defaults to creator from config."),
    node_type: str = typer.Option("snapshot", "--type", help="'snapshot' (or 'snap') or 'live'."),
    content: str = typer.Option("", "--content", help="Optional Markdown body."),
    status: str | None = typer.Option(None, "--status", help="active (default), archived (done — keep for history), or suppressed (temporarily hidden — still relevant)."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Create a context node in a pool."""
    _apply_verbose(verbose)
    cfg = _load_cli_config()
    pool_str = _require_pool(pool, cfg)
    resolved_creator = _require_creator(creator, cfg)
    with _pool_context(pool_str, cfg, writable=True) as resolved_pool:
        result = create_node(
            pool_path=resolved_pool,
            source=_CLI_SOURCE,
            node_type=node_type,
            context=context,
            creator=resolved_creator,
            content=content,
            status=status,
            auto_commit=cfg.auto_commit,
        )
    if result.duplicate:
        console.print(
            f"[yellow]duplicate:[/yellow] node already exists "
            f"(created by {result.existing_creator})"
        )
        raise typer.Exit(code=0)
    console.print(f"[green]node created:[/green] {result.node_id}")
    console.print(f"  path: {result.path}")
    _auto_push_if_configured(pool_str, cfg)


@app.command("list")
def cmd_list(
    pool: str | None = typer.Option(None, "--pool", "-p", help="Pool path or name. Defaults to default_pool from config.", autocompletion=_complete_pool),
    status: list[str] = typer.Option(
        [],
        "-s",
        "--status",
        help="Filter by status: archived, suppressed, all, or comma-separated e.g. archived,suppressed.",
    ),
    output: str = typer.Option("console", "-o", "--output", help="Output format: console (default), json, yaml, csv."),
    pull: bool = typer.Option(False, "--pull", help="Pull latest changes before listing (RW clones only)."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """List nodes in a pool with frontmatter summary.

    By default only active nodes are shown. Use -s to filter by a specific status:
    -s archived            done — kept for history; excluded from active work
    -s suppressed          temporarily hidden — still relevant, just noisy right now
    -s archived,suppressed show only archived and suppressed
    -s all                 show everything regardless of status
    """
    _apply_verbose(verbose)
    cfg = _load_cli_config()
    pool_str = _require_pool(pool, cfg)
    _pull_if_requested(pool_str, cfg, pull=pull)

    # Flatten any comma-separated values passed to -s.
    raw: set[str] = set()
    for token in status:
        raw.update(v.strip() for v in token.split(",") if v.strip())

    if not raw:
        include_statuses: set[str] = {"active"}
    elif "all" in raw:
        include_statuses = {"active", "archived", "suppressed"}
    else:
        include_statuses = raw

    with _pool_context(pool_str, cfg) as resolved_pool:
        summaries = list_nodes(
            resolved_pool, include_statuses=include_statuses,
        )

    fmt = output.lower().strip()

    if fmt == "json":
        print(json.dumps([
            {"id": s.node_id, "type": s.node_type, "status": s.status,
             "context": s.context, "timestamp": s.timestamp}
            for s in summaries
        ], indent=2))
        return

    if fmt == "yaml":
        import yaml as _yaml
        print(_yaml.dump(
            [{"id": s.node_id, "type": s.node_type, "status": s.status,
              "context": s.context, "timestamp": s.timestamp}
             for s in summaries],
            default_flow_style=False, allow_unicode=True,
        ), end="")
        return

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "type", "status", "context", "timestamp"])
        for s in summaries:
            writer.writerow([
                s.node_id, s.node_type, s.status,
                s.context, s.timestamp,
            ])
        print(buf.getvalue(), end="")
        return

    # Console output (default).
    _display_pool = pool_str
    pool_name = Path(pool_str).name if not is_remote_registry(pool_str) else pool_str
    registry_label = None
    if not is_remote_registry(pool_str):
        rp = Path(pool_str)
        for reg_id, entry in cfg.registries.items():
            if not is_remote_registry(entry.pool_home) and rp.parent == Path(entry.pool_home):
                registry_label = reg_id
                pool_name = rp.name
                break
    if registry_label:
        console.print(
            f"[dim]registry:[/dim] {registry_label}  "
            f"[dim]pool:[/dim] {pool_name}"
        )
    else:
        console.print(f"[dim]pool:[/dim] {_display_pool}")

    if not summaries:
        console.print("no nodes found.")
        return
    table = Table(show_header=True, header_style="bold", row_styles=["", "dim"], expand=False)
    table.add_column("ID", style="dim", width=14)
    table.add_column("type", width=7)
    table.add_column("status", width=10)
    table.add_column("context", max_width=80)
    table.add_column("timestamp", width=22)
    for s in summaries:
        display_type = "snap" if s.node_type == "snapshot" else s.node_type
        table.add_row(
            s.node_id, display_type, s.status, s.context, s.timestamp,
        )
    console.print(table)


@app.command("show")
def cmd_show(
    node_id: str = typer.Argument(..., help="Node ID to display."),
    pool: str | None = typer.Option(None, "--pool", "-p", help="Pool path or name. Defaults to default_pool from config.", autocompletion=_complete_pool),
    pull: bool = typer.Option(False, "--pull", help="Pull latest changes before showing (RW clones only)."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Display full node content formatted for terminal."""
    _apply_verbose(verbose)
    cfg = _load_cli_config()
    pool_str = _require_pool(pool, cfg)
    _pull_if_requested(pool_str, cfg, pull=pull)
    with _pool_context(pool_str, cfg) as resolved_pool:
        detail = show_node(resolved_pool, node_id)
    if detail is None:
        console.print(f"[red]not found:[/red] {node_id}")
        raise typer.Exit(code=1)
    console.print(f"[bold]id:[/bold]        {detail.node_id}")
    console.print(f"[bold]context:[/bold]   {detail.context}")
    console.print(f"[bold]type:[/bold]      {detail.node_type}")
    console.print(f"[bold]source:[/bold]    {detail.source}")
    console.print(f"[bold]creator:[/bold]   {detail.creator}")
    console.print(f"[bold]timestamp:[/bold] {detail.timestamp}")
    if detail.tags:
        console.print(f"[bold]tags:[/bold]      {', '.join(detail.tags)}")
    if detail.related_to:
        console.print(f"[bold]related:[/bold]   {', '.join(detail.related_to)}")
    if detail.body:
        console.print(f"\n{detail.body}")


@app.command("validate")
def cmd_validate(
    pool: str | None = typer.Option(None, "--pool", "-p", help="Pool path or name. Defaults to default_pool from config.", autocompletion=_complete_pool),
    pull: bool = typer.Option(False, "--pull", help="Pull latest changes before validating (RW clones only)."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Check all nodes in a pool against schema."""
    _apply_verbose(verbose)
    cfg = _load_cli_config()
    pool_str = _require_pool(pool, cfg)
    _pull_if_requested(pool_str, cfg, pull=pull)

    # For local pools, check existence before entering context.
    if not is_remote_registry(pool_str) and not Path(pool_str).exists():
        console.print(f"[red]error:[/red] pool not found: {pool_str}")
        raise typer.Exit(code=1)

    errors_found = False
    node_count = 0
    with _pool_context(pool_str, cfg) as resolved_pool:
        for subdir in ("snapshots", "live"):
            directory = resolved_pool / subdir
            if not directory.exists():
                continue
            for node_file in sorted(directory.glob("*.md")):
                node_count += 1
                frontmatter = extract_frontmatter(node_file.read_text())
                if frontmatter is None:
                    console.print(
                        f"[red]no frontmatter:[/red] {node_file.name}",
                    )
                    errors_found = True
                    continue
                vresult = validate_node(frontmatter)
                if not vresult.valid:
                    errors_found = True
                    for error in vresult.errors:
                        console.print(
                            f"[red]invalid:[/red] {node_file.name}: {error}",
                        )
    pool_name = (
        Path(pool_str).name if not is_remote_registry(pool_str)
        else pool_str
    )

    # For local pools with auto_pull or auto_push, check git health.
    if not is_remote_registry(pool_str):
        entry = _find_entry_for_pool(pool_str, cfg)
        if entry and (entry.auto_pull or entry.auto_push):
            pool_root = Path(pool_str)
            # Walk up to find the git root (pool may be a subdir of a repo).
            git_root = pool_root
            while git_root != git_root.parent:
                if (git_root / ".git").is_dir():
                    break
                git_root = git_root.parent
            git_result = check_git_state(git_root)
            if not git_result.valid:
                for error in git_result.errors:
                    console.print(f"[yellow]git warning:[/yellow] {error}")
                errors_found = True

    if errors_found:
        raise typer.Exit(code=1)
    if node_count == 0:
        console.print(f"no nodes found in pool {pool_name}.")
    else:
        console.print(
            f"[green]{node_count} node{'s' if node_count != 1 else ''}"
            f" in pool {pool_name} valid.[/green]"
        )


@config_app.callback()
def _config_default(ctx: typer.Context) -> None:
    """Config file commands. Defaults to 'list' when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(config_list, cwd=None, verbose=False)


@config_app.command("list")
def config_list(
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for config path walk."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """List all config files in the discovery tree.

    Shows every path that alph checks when loading config — the global config
    plus every config.yaml found walking up from the current directory. Each
    entry shows whether the file exists and which registry IDs it declares.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    summaries = list_config_paths(global_config_dir=_global_config_dir(), cwd=resolved_cwd)
    table = Table(show_header=True, header_style="bold", row_styles=["", "dim"])
    table.add_column("config file", min_width=40)
    table.add_column("status", width=8)
    table.add_column("type", width=8)
    table.add_column("registries")
    for s in summaries:
        if _verbose and not s.is_global:
            display_path = os.path.relpath(s.path, resolved_cwd)
        else:
            display_path = str(s.path)
        status = "[green]exists[/green]" if s.exists else "[dim]missing[/dim]"
        kind = "[cyan]global[/cyan]" if s.is_global else "local"
        reg_ids = ", ".join(s.registry_ids) if s.registry_ids else "[dim]—[/dim]"
        table.add_row(display_path, status, kind, reg_ids)
    console.print(table)
    console.print(
        "\n[dim]global config is read first (base); "
        "local configs override from root → cwd (most specific wins)[/dim]"
    )


@config_app.command("show")
def config_show(
    config_path: Path = typer.Argument(..., help="Path to a config.yaml file to display."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Display a config file with syntax highlighting.

    If the file exists, its YAML content is printed with syntax highlighting.
    If the file does not exist, a commented template is shown along with
    instructions for bootstrapping a registry at the same location.
    """
    _apply_verbose(verbose)
    if config_path.exists():
        console.print(f"[bold]{config_path}[/bold]\n")
        console.print(Syntax(config_path.read_text(), "yaml", theme="monokai", line_numbers=False))
    else:
        console.print(f"[yellow]not found:[/yellow] {config_path}")
        console.print("  run [bold]alph registry init[/bold] to create a registry and generate this file.")
        console.print("  run [bold]alph defaults[/bold] to see what is currently configured.")


@config_app.command("check")
def config_check(
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for config path walk."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Check all config files for unknown or legacy keys, and referential integrity.

    Reads every config file in the discovery tree and reports unrecognized
    keys that may indicate typos (e.g. 'clone_dir' instead of 'clone_path').
    Also checks that defaults like default_registry point to registries that
    actually exist in the merged config.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    summaries = list_config_paths(global_config_dir=_global_config_dir(), cwd=resolved_cwd)
    all_warnings: list[str] = []
    for s in summaries:
        if not s.exists:
            continue
        import yaml as _yaml
        data = _yaml.safe_load(s.path.read_text()) or {}
        if not isinstance(data, dict):
            continue
        warnings = validate_config_keys(data)
        for w in warnings:
            all_warnings.append(f"{s.path}: {w}")
    cfg = _load_cli_config(cwd=resolved_cwd)
    from alph.core import validate_config_integrity
    for w in validate_config_integrity(cfg):
        all_warnings.append(w)
    if all_warnings:
        for w in all_warnings:
            console.print(f"[yellow]warning:[/yellow] {w}")
        console.print(f"\n[red]{len(all_warnings)} issue{'s' if len(all_warnings) != 1 else ''} found.[/red]")
        raise typer.Exit(code=1)
    console.print("[green]ok:[/green] all config files use recognized keys.")


@config_app.command("show-all")
def config_show_all(
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for config lookup."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Display the fully merged config with all default values filled in.

    Shows the resolved configuration as alph sees it — including implicit
    defaults for auto_commit, auto_push, auto_pull, mode, etc. Useful for
    inspecting which values are in effect without reading multiple files.
    """
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)

    import yaml as _yaml

    regs: dict[str, object] = {}
    for reg_id, entry in cfg.registries.items():
        reg_dict: dict[str, object] = {
            "pool_home": entry.pool_home,
            "context": entry.context,
            "name": entry.name,
            "mode": effective_mode(entry),
            "clone_path": entry.clone_path,
            "branch": entry.branch,
            "auto_push": entry.auto_push,
            "auto_pull": entry.auto_pull,
        }
        if entry.pools:
            reg_dict["pools"] = dict(entry.pools)
        regs[reg_id] = reg_dict

    full: dict[str, object] = {
        "creator": cfg.creator,
        "auto_commit": cfg.auto_commit,
        "default_registry": cfg.default_registry,
        "default_pool": cfg.default_pool,
        "register_subdir_pools": cfg.register_subdir_pools,
        "registries": regs,
    }
    text = _yaml.dump(full, default_flow_style=False, sort_keys=False)
    console.print("[bold]merged config (all defaults resolved)[/bold]\n")
    console.print(Syntax(text, "yaml", theme="monokai", line_numbers=False))


@app.command("defaults")
def cmd_defaults(
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for config lookup."),
    verbose: bool = _VERBOSE_OPT,
) -> None:
    """Show the currently resolved defaults (registry, pool, creator)."""
    _apply_verbose(verbose)
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)

    def _val(v: str, fallback: str = "[dim]not set[/dim]") -> str:
        return v if v else fallback

    console.print("[bold]alph defaults[/bold]")
    console.print(f"  creator:          {_val(cfg.creator)}")
    console.print(f"  default_registry: {_val(cfg.default_registry)}")
    console.print(f"  default_pool:     {_val(cfg.default_pool)}")
    console.print(f"  auto_commit:      {cfg.auto_commit}")
    console.print(f"  register_subdir_pools: {cfg.register_subdir_pools}")

    if cfg.default_registry and cfg.default_registry in cfg.registries:
        entry = cfg.registries[cfg.default_registry]
        pool_path = (
            str(Path(entry.pool_home) / cfg.default_pool) if cfg.default_pool else "[dim]not set[/dim]"
        )
        console.print(f"  resolved pool:    {pool_path}")

    console.print()
    console.print("  run [bold]alph config list[/bold] to see which config files are active.")


@app.command("examples", hidden=True)
def cmd_examples() -> None:
    """Show structured usage examples for common workflows."""
    console.print(
        """[bold]AlpheusCEF — Usage Examples[/bold]

[bold underline]1. Getting started: local registry[/bold underline]

  [dim]# Create a registry rooted at ~/context[/dim]
  alph registry init --pool-home ~/context --id personal -c "Personal context"

  [dim]# Create a pool for a project[/dim]
  alph pool init --name kitchen-remodel -c "Planning and decisions for kitchen remodel"

  [dim]# Add a snapshot node capturing a decision[/dim]
  alph add -c "Decided on quartz countertops after comparing durability and cost" \\
      --content "Granite was $200/sqft more and the installer said quartz is \\
  easier to maintain. Went with Caesarstone color 5143."

  [dim]# List nodes in the pool[/dim]
  alph list

  [dim]# Show full content of a node[/dim]
  alph show a1b2c3d4e5f6

[bold underline]2. Multiple pools, one registry[/bold underline]

  [dim]# All pools live as subdirectories of the registry home[/dim]
  alph pool init --name vehicles -c "Maintenance for the family cars"
  alph pool init --name standards -c "Cross-cutting household standards"

  [dim]# Add a node to a specific pool[/dim]
  alph add -p vehicles -c "Oil change on Highlander at 45k miles"
  alph add -p standards -c "All major purchases require two quotes minimum"

  [dim]# List pools to see what exists[/dim]
  alph pool list

[bold underline]3. Remote registry (read-only)[/bold underline]

  [dim]# Point at a shared team repo — no clone needed for reads[/dim]
  alph registry init \\
      --pool-home git@github.com:org/shared-context.git:/registry \\
      --id team-shared -c "Shared engineering context" \\
      --mode ro --branch main

  [dim]# List nodes from the remote pool[/dim]
  alph list -r team-shared -p standards

  [dim]# Check that the remote is reachable[/dim]
  alph registry check team-shared

[bold underline]4. Remote registry (read-write)[/bold underline]

  [dim]# Register a remote repo with a local clone for writes[/dim]
  alph registry init \\
      --pool-home git@github.com:myorg/project-context.git \\
      --id project -c "Project decisions and context" \\
      --mode rw --clone-path ~/git/project-context --branch main

  [dim]# Clone it locally[/dim]
  alph registry clone project

  [dim]# Now you can add nodes — they go into the local clone[/dim]
  alph add -r project -p decisions -c "Chose gRPC over REST for internal APIs"

  [dim]# Pull latest from remote[/dim]
  alph registry pull project

[bold underline]5. Inspecting your setup[/bold underline]

  [dim]# What registries do I have?[/dim]
  alph registry list

  [dim]# What are my current defaults?[/dim]
  alph defaults

  [dim]# Show fully merged config with all defaults resolved[/dim]
  alph config show-all

  [dim]# Check config files for typos or unknown keys[/dim]
  alph config check

  [dim]# Show a specific config file[/dim]
  alph config show ~/.config/alph/config.yaml

[bold underline]6. Filtering and output formats[/bold underline]

  [dim]# Only archived nodes[/dim]
  alph list -s archived

  [dim]# All statuses[/dim]
  alph list -s all

  [dim]# JSON output for scripting[/dim]
  alph list -o json

  [dim]# CSV for spreadsheets[/dim]
  alph list -o csv > nodes.csv

[bold underline]7. Short aliases[/bold underline]

  [dim]# These are equivalent:[/dim]
  alph add -c "..."        [dim]#  alph a -c "..."[/dim]
  alph list                 [dim]#  alph l[/dim]
  alph show <id>            [dim]#  alph s <id>[/dim]
  alph validate             [dim]#  alph v[/dim]
  alph registry list        [dim]#  alph reg list  or just  alph reg[/dim]
  alph --registry X list    [dim]#  alph -r X list  or  alph --reg X list[/dim]
  alph list --pool Y        [dim]#  alph list -p Y[/dim]

[dim]See also: man alph[/dim]"""
    )


# Short command aliases (hidden from --help)
app.command("l", hidden=True)(cmd_list)
app.command("a", hidden=True)(cmd_add)
app.command("s", hidden=True)(cmd_show)
app.command("v", hidden=True)(cmd_validate)
