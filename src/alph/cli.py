"""Thin Typer wrapper exposing core.py as the `alph` CLI."""

import logging
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from alph.core import (
    AlphConfig,
    collect_registries,
    create_node,
    default_global_config_text,
    extract_frontmatter,
    init_pool,
    init_registry,
    list_config_paths,
    list_nodes,
    load_config,
    resolve_default_pool,
    resolve_pool_name,
    show_node,
    validate_node,
)

_help_settings = {"help_option_names": ["-h", "--help"]}
app = typer.Typer(name="alph", help="Alpheus Context Engine Framework.", context_settings=_help_settings)
registry_app = typer.Typer(help="Registry commands.", context_settings=_help_settings)
pool_app = typer.Typer(help="Pool commands.", context_settings=_help_settings)
config_app = typer.Typer(help="Config file commands.", context_settings=_help_settings)
app.add_typer(registry_app, name="registry")
app.add_typer(pool_app, name="pool")
app.add_typer(config_app, name="config")

console = Console(width=200)

_verbose: bool = False


@app.callback()
def _main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging.", is_eager=False),
) -> None:
    """Alpheus Context Engine Framework."""
    global _verbose
    _verbose = verbose
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _global_config_dir() -> Path:
    """Return the global config directory, overridable via ALPH_CONFIG_DIR for tests."""
    override = os.environ.get("ALPH_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".config" / "alph"


def _load_cli_config(cwd: Path | None = None) -> AlphConfig:
    """Load merged config for a CLI invocation."""
    return load_config(
        global_config_dir=_global_config_dir(),
        cwd=cwd or Path.cwd(),
    )


def _require_pool(pool_flag: str | None, cfg: AlphConfig) -> Path:
    """Resolve pool path from flag or config default. Exits with error if neither is set.

    The flag value is resolved in order:
    1. Absolute path or existing relative path — used as-is.
    2. Pool name found in a registry — resolved to registry_home/name.
    3. Falls back to default_registry/default_pool from config.
    """
    if pool_flag is not None:
        p = Path(pool_flag)
        if p.is_absolute() or p.exists():
            return p
        by_name = resolve_pool_name(pool_flag, cfg)
        if by_name is not None:
            return by_name
        console.print(
            f"[red]error:[/red] --pool '{pool_flag}' is not a path and was not found "
            "as a pool name in any known registry"
        )
        raise typer.Exit(code=1)
    resolved = resolve_default_pool(cfg)
    if resolved is None:
        console.print(
            "[red]error:[/red] --pool required, or set default_registry + default_pool "
            "and registries in ~/.config/alph/config.yaml"
        )
        raise typer.Exit(code=1)
    return resolved


def _require_creator(creator_flag: str | None, cfg: AlphConfig) -> str:
    """Resolve creator from flag or config. Exits with error if neither is set."""
    if creator_flag:
        return creator_flag
    if cfg.creator:
        return cfg.creator
    console.print("[red]error:[/red] --creator required, or set creator in ~/.config/alph/config.yaml")
    raise typer.Exit(code=1)


@registry_app.command("init")
def registry_init(
    home: Path = typer.Option(..., "--home", help="Directory where pool subdirectories will be created. The registry definition is written into the global config, not here."),
    registry_id: str = typer.Option(..., "--id", help="Machine identifier for the registry."),
    context: str = typer.Option(..., "--context", "-c", help="Human/LLM-readable description."),
    name: str = typer.Option("", "--name", help="Optional human-readable name."),
) -> None:
    """Create a registry home directory and register it in the global config.

    The registry definition (id, context, name) is written into the global
    config (~/.config/alph/config.yaml). The --home directory is created but
    receives no config file — it is just the directory where pool subdirectories
    will live.

    If no default registry is set yet, this one becomes the default, enabling
    'alph add' and 'alph list' to work without --pool or --creator flags
    (once creator and default_pool are also configured).
    """
    result = init_registry(
        home=home,
        registry_id=registry_id,
        context=context,
        name=name,
        global_config_dir=_global_config_dir(),
    )
    if not result.valid:
        for error in result.errors:
            console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(code=1)
    console.print(f"[green]registry created:[/green] {registry_id}")
    console.print(f"  home:   {home}")
    console.print(f"  config: {result.config_path}")
    if result.set_as_default:
        console.print("  [dim]set as default registry[/dim]")
    else:
        console.print("  [dim](not set as default — another default registry already exists)[/dim]")


@registry_app.command("list")
def registry_list(
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for config lookup."),
) -> None:
    """List all registries known to alph from the config file tree."""
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    cfg = _load_cli_config(cwd=resolved_cwd)
    summaries = collect_registries(cfg=cfg)
    if not summaries:
        console.print("no registries found.")
        console.print("  run [bold]alph registry init[/bold] to create one.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=20)
    table.add_column("name", width=20)
    table.add_column("context")
    table.add_column("home")
    for s in summaries:
        table.add_row(s.registry_id, s.name, s.context, str(s.home_path))
    console.print(table)


@pool_app.command("init")
def pool_init(
    registry: str | None = typer.Option(None, "--registry", help="Registry ID or name. Defaults to default_registry from config."),
    name: str = typer.Option(..., "--name", help="Pool name (machine identifier)."),
    context: str = typer.Option(..., "--context", "-c", help="Human/LLM-readable description."),
    pool_type: str = typer.Option("subdir", "--type", help="Pool type: 'subdir' (pool is a subdirectory of the registry home) or 'repo' (standalone git repository)."),
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for registry lookup."),
    bootstrap: bool = typer.Option(False, "--bootstrap", hidden=True, help="Create registry if not found."),
    registry_context: str = typer.Option("", "--registry-context", hidden=True, help="Context for bootstrapped registry."),
) -> None:
    """Create a pool inside a registry, register it, and validate it.

    The registry is located by ID or name, walking up from the current
    directory (or --cwd) and checking the global config. Use
    'alph registry list' to see which registries are known.
    """
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


@app.command("add")
def cmd_add(
    context: str = typer.Option(..., "-c", "--context", help="Context description for this node."),
    pool: str | None = typer.Option(None, "--pool", help="Pool path or name. Defaults to default_pool from config."),
    creator: str | None = typer.Option(None, "--creator", help="Creator email address. Defaults to creator from config."),
    node_type: str = typer.Option("fixed", "--type", help="'fixed' or 'live'."),
    content: str = typer.Option("", "--content", help="Optional Markdown body."),
    status: str | None = typer.Option(None, "--status", help="active, archived, or suppressed."),
) -> None:
    """Create a context node in a pool."""
    cfg = _load_cli_config()
    resolved_pool = _require_pool(pool, cfg)
    resolved_creator = _require_creator(creator, cfg)
    result = create_node(
        pool_path=resolved_pool,
        source="cli",
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


@app.command("list")
def cmd_list(
    pool: str | None = typer.Option(None, "--pool", help="Pool path or name. Defaults to default_pool from config."),
    status: list[str] = typer.Option(
        [],
        "-s",
        "--status",
        help="Expand beyond active: archived, suppressed, or all.",
    ),
) -> None:
    """List nodes in a pool with frontmatter summary.

    By default only active nodes are shown. Use -s to expand:
    -s archived   includes active + archived
    -s suppressed includes active + suppressed
    -s all        includes everything
    """
    cfg = _load_cli_config()
    resolved_pool = _require_pool(pool, cfg)

    if not status:
        include_statuses: set[str] = {"active"}
    elif "all" in status:
        include_statuses = {"active", "archived", "suppressed"}
    else:
        include_statuses = {"active"} | set(status)

    summaries = list_nodes(resolved_pool, include_statuses=include_statuses)
    if not summaries:
        console.print("no nodes found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=14)
    table.add_column("type", width=7)
    table.add_column("status", width=10)
    table.add_column("context")
    table.add_column("timestamp", width=22)
    for s in summaries:
        table.add_row(s.node_id, s.node_type, s.status, s.context, s.timestamp)
    console.print(table)


@app.command("show")
def cmd_show(
    node_id: str = typer.Argument(..., help="Node ID to display."),
    pool: str | None = typer.Option(None, "--pool", help="Pool path or name. Defaults to default_pool from config."),
) -> None:
    """Display full node content formatted for terminal."""
    cfg = _load_cli_config()
    resolved_pool = _require_pool(pool, cfg)
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
    pool: str | None = typer.Option(None, "--pool", help="Pool path or name. Defaults to default_pool from config."),
) -> None:
    """Check all nodes in a pool against schema."""
    cfg = _load_cli_config()
    resolved_pool = _require_pool(pool, cfg)
    errors_found = False
    for subdir in ("snapshots", "pointers"):
        directory = resolved_pool / subdir
        if not directory.exists():
            continue
        for node_file in sorted(directory.glob("*.md")):
            frontmatter = extract_frontmatter(node_file.read_text())
            if frontmatter is None:
                console.print(f"[red]no frontmatter:[/red] {node_file.name}")
                errors_found = True
                continue
            vresult = validate_node(frontmatter)
            if not vresult.valid:
                errors_found = True
                for error in vresult.errors:
                    console.print(f"[red]invalid:[/red] {node_file.name}: {error}")
    if not errors_found:
        console.print("[green]all nodes valid.[/green]")
    else:
        raise typer.Exit(code=1)


@config_app.command("list")
def config_list(
    cwd: Path | None = typer.Option(None, "--cwd", hidden=True, help="Working directory for config path walk."),
) -> None:
    """List all config files in the discovery tree.

    Shows every path that alph checks when loading config — the global config
    plus every config.yaml found walking up from the current directory. Each
    entry shows whether the file exists and which registry IDs it declares.
    """
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    summaries = list_config_paths(global_config_dir=_global_config_dir(), cwd=resolved_cwd)
    table = Table(show_header=True, header_style="bold")
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
) -> None:
    """Display a config file with syntax highlighting.

    If the file exists, its YAML content is printed with syntax highlighting.
    If the file does not exist, a commented template is shown along with
    instructions for bootstrapping a registry at the same location.
    """
    if config_path.exists():
        console.print(f"[bold]{config_path}[/bold]\n")
        console.print(Syntax(config_path.read_text(), "yaml", theme="monokai", line_numbers=False))
    else:
        console.print(f"[yellow]not found:[/yellow] {config_path}\n")
        console.print(
            "No config file exists at this path yet. To bootstrap a registry\n"
            "and create the global config in one step:\n\n"
            "  [bold]alph registry init \\\\\n"
            "    --home <registry-dir> \\\\\n"
            "    --id <registry-id> \\\\\n"
            "    --context \"<description>\"[/bold]\n\n"
            "This writes the registry path into the global config and sets it\n"
            "as the default. Or create the config file manually using this template:\n"
        )
        console.print(Syntax(default_global_config_text(), "yaml", theme="monokai", line_numbers=False))
