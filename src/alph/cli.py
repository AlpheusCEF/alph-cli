"""Thin Typer wrapper exposing core.py as the `alph` CLI."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from alph.core import (
    create_node,
    extract_frontmatter,
    init_pool,
    init_registry,
    list_nodes,
    show_node,
    validate_node,
)

app = typer.Typer(name="alph", help="Alpheus Context Engine Framework.")
registry_app = typer.Typer(help="Registry commands.")
pool_app = typer.Typer(help="Pool commands.")
app.add_typer(registry_app, name="registry")
app.add_typer(pool_app, name="pool")

console = Console(width=200)


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
    registry: Path = typer.Option(..., "--registry", help="Path to the parent registry."),
    name: str = typer.Option(..., "--name", help="Pool name (machine identifier)."),
    context: str = typer.Option(..., "--context", "-c", help="Human/LLM-readable description."),
    layout: str = typer.Option("subdirectory", "--layout", help="'subdirectory' or 'repo'."),
) -> None:
    """Create a pool, register it, validate it, and print defaults."""
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
    pool: Path = typer.Option(..., "--pool", help="Path to the pool directory."),
    creator: str = typer.Option(..., "--creator", help="Creator email address."),
    node_type: str = typer.Option("fixed", "--type", help="'fixed' or 'live'."),
    content: str = typer.Option("", "--content", help="Optional Markdown body."),
    status: str | None = typer.Option(None, "--status", help="active, archived, or suppressed."),
) -> None:
    """Create a context node in a pool."""
    result = create_node(
        pool_path=pool,
        source="cli",
        node_type=node_type,
        context=context,
        creator=creator,
        content=content,
        status=status,
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
    pool: Path = typer.Option(..., "--pool", help="Path to the pool directory."),
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
    if not status:
        include_statuses: set[str] = {"active"}
    elif "all" in status:
        include_statuses = {"active", "archived", "suppressed"}
    else:
        include_statuses = {"active"} | set(status)

    summaries = list_nodes(pool, include_statuses=include_statuses)
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
    pool: Path = typer.Option(..., "--pool", help="Path to the pool directory."),
) -> None:
    """Display full node content formatted for terminal."""
    detail = show_node(pool, node_id)
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
    pool: Path = typer.Option(..., "--pool", help="Path to the pool directory."),
) -> None:
    """Check all nodes in a pool against schema."""
    errors_found = False
    for subdir in ("snapshots", "pointers"):
        directory = pool / subdir
        if not directory.exists():
            continue
        for node_file in sorted(directory.glob("*.md")):
            frontmatter = extract_frontmatter(node_file.read_text())
            if frontmatter is None:
                console.print(f"[red]no frontmatter:[/red] {node_file.name}")
                errors_found = True
                continue
            result = validate_node(frontmatter)
            if not result.valid:
                errors_found = True
                for error in result.errors:
                    console.print(f"[red]invalid:[/red] {node_file.name}: {error}")
    if not errors_found:
        console.print("[green]all nodes valid.[/green]")
    else:
        raise typer.Exit(code=1)
