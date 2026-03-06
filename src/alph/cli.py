"""Thin Typer wrapper exposing core.py as the `alph` CLI."""

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from alph.core import (
    AlphConfig,
    create_node,
    extract_frontmatter,
    init_pool,
    init_registry,
    list_nodes,
    load_config,
    resolve_default_pool,
    show_node,
    validate_node,
)

_help_settings = {"help_option_names": ["-h", "--help"]}
app = typer.Typer(name="alph", help="Alpheus Context Engine Framework.", context_settings=_help_settings)
registry_app = typer.Typer(help="Registry commands.", context_settings=_help_settings)
pool_app = typer.Typer(help="Pool commands.", context_settings=_help_settings)
app.add_typer(registry_app, name="registry")
app.add_typer(pool_app, name="pool")

console = Console(width=200)


def _global_config_dir() -> Path:
    """Return the global config directory, overridable via ALPH_CONFIG_DIR for tests."""
    override = os.environ.get("ALPH_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".config" / "alph"


def _load_cli_config(pool_path: Path | None = None) -> AlphConfig:
    """Load merged config for a CLI invocation."""
    return load_config(
        global_config_dir=_global_config_dir(),
        pool_path=pool_path or Path("/nonexistent"),
    )


def _require_pool(pool_flag: Path | None, cfg: AlphConfig) -> Path:
    """Resolve pool path from flag or config default. Exits with error if neither is set."""
    if pool_flag is not None:
        return pool_flag
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
    path: Path = typer.Option(..., "--path", help="Directory to create as the registry root."),
    registry_id: str = typer.Option(..., "--id", help="Machine identifier for the registry."),
    context: str = typer.Option(..., "--context", "-c", help="Human/LLM-readable description."),
    name: str = typer.Option("", "--name", help="Optional human-readable name."),
) -> None:
    """Create a registry, validate it, and print what was created."""
    result = init_registry(path=path, registry_id=registry_id, context=context, name=name)
    if not result.valid:
        for error in result.errors:
            console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(code=1)
    console.print(f"[green]registry created:[/green] {result.config_path}")


@pool_app.command("init")
def pool_init(
    registry: Path | None = typer.Option(None, "--registry", help="Path to the parent registry. Defaults to default_registry from config."),
    name: str = typer.Option(..., "--name", help="Pool name (machine identifier)."),
    context: str = typer.Option(..., "--context", "-c", help="Human/LLM-readable description."),
    layout: str = typer.Option("subdirectory", "--layout", help="'subdirectory' or 'repo'."),
) -> None:
    """Create a pool, register it, validate it, and print defaults."""
    cfg = _load_cli_config()
    if registry is None:
        reg_id = cfg.default_registry
        reg_path_str = cfg.registries.get(reg_id) if reg_id else None
        if not reg_path_str:
            console.print(
                "[red]error:[/red] --registry required, or set default_registry and registries "
                "in ~/.config/alph/config.yaml"
            )
            raise typer.Exit(code=1)
        registry = Path(reg_path_str)
    result = init_pool(registry_path=registry, name=name, context=context, layout=layout)
    if not result.valid:
        for error in result.errors:
            console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(code=1)
    console.print(f"[green]pool created:[/green] {result.pool_path}")
    console.print(f"  snapshots/  {result.pool_path / 'snapshots'}")
    console.print(f"  pointers/   {result.pool_path / 'pointers'}")
    console.print(f"  .alph/      {result.pool_path / '.alph'}")


@app.command("add")
def cmd_add(
    context: str = typer.Option(..., "-c", "--context", help="Context description for this node."),
    pool: Path | None = typer.Option(None, "--pool", help="Path to the pool directory. Defaults to default_pool from config."),
    creator: str | None = typer.Option(None, "--creator", help="Creator email address. Defaults to creator from config."),
    node_type: str = typer.Option("fixed", "--type", help="'fixed' or 'live'."),
    content: str = typer.Option("", "--content", help="Optional Markdown body."),
    status: str | None = typer.Option(None, "--status", help="active, archived, or suppressed."),
) -> None:
    """Create a context node in a pool."""
    cfg = _load_cli_config(pool)
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
    pool: Path | None = typer.Option(None, "--pool", help="Path to the pool directory. Defaults to default_pool from config."),
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
    cfg = _load_cli_config(pool)
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
    pool: Path | None = typer.Option(None, "--pool", help="Path to the pool directory. Defaults to default_pool from config."),
) -> None:
    """Display full node content formatted for terminal."""
    cfg = _load_cli_config(pool)
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
    pool: Path | None = typer.Option(None, "--pool", help="Path to the pool directory. Defaults to default_pool from config."),
) -> None:
    """Check all nodes in a pool against schema."""
    cfg = _load_cli_config(pool)
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
