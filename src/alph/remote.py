"""Remote registry access — read-only API providers and clone management.

RO mode: providers list and read pool node files from a remote forge
without a local clone. The ``resolve_pool_readonly`` context manager
fetches files to an ephemeral tmpdir.

RW mode: ``clone_remote_registry`` and ``pull_remote_registry`` manage
persistent local clones for write operations.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileEntry:
    """A file entry returned by a provider's list_files call."""

    name: str  # filename (e.g. "abc123.md")
    path: str  # full path in repo (e.g. "registry/vehicles/snapshots/abc123.md")
    file_type: str  # "blob" for files, "tree" for directories


class RemoteProvider(Protocol):
    """Protocol for read-only access to a remote git repository."""

    def list_files(self, path: str, ref: str = "HEAD") -> list[FileEntry]:
        """List files in a directory within the repo."""
        ...

    def read_file(self, path: str, ref: str = "HEAD") -> str:
        """Read the content of a single file."""
        ...

    def read_files(self, paths: list[str], ref: str = "HEAD") -> dict[str, str]:
        """Batch-read multiple files. Returns mapping of path -> content."""
        ...


# ---------------------------------------------------------------------------
# SSH host alias resolution
# ---------------------------------------------------------------------------

_DEFAULT_SSH_CONFIG = Path.home() / ".ssh" / "config"


def _resolve_ssh_hostname(
    alias: str,
    *,
    ssh_config_path: Path | None = None,
) -> str | None:
    """Resolve an SSH host alias to its HostName via ~/.ssh/config.

    Parses the SSH config file looking for a ``Host`` entry matching
    *alias* (exact, non-wildcard) and returns its ``HostName`` value.
    Returns ``None`` if the alias is not found or the config file
    does not exist.
    """
    config_path = ssh_config_path or _DEFAULT_SSH_CONFIG
    if not config_path.is_file():
        return None

    try:
        text = config_path.read_text()
    except OSError:
        return None

    current_host: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue

        keyword, value = parts[0].lower(), parts[1]

        if keyword == "host":
            # Skip wildcard entries; only match exact alias.
            current_host = None if "*" in value or "?" in value else value.strip()
        elif keyword == "hostname" and current_host == alias:
            return value.strip().lower()

    return None


# ---------------------------------------------------------------------------
# Forge detection
# ---------------------------------------------------------------------------


def _extract_host(remote_url: str) -> str:
    """Extract the host portion from a git remote URL."""
    if remote_url.startswith("git@"):
        match = re.match(r"git@([^:]+):", remote_url)
        if match:
            return match.group(1)
    elif "://" in remote_url:
        match = re.match(r"[a-z+]+://(?:[^@]+@)?([^/]+)", remote_url)
        if match:
            return match.group(1)
    return ""


def detect_forge(
    remote_url: str,
    *,
    ssh_config_path: Path | None = None,
) -> str:
    """Detect the git forge from a remote URL.

    Resolves SSH host aliases via ``~/.ssh/config`` so that
    ``git@github-personal:org/repo.git`` is correctly identified
    when ``github-personal`` maps to ``github.com``.

    Returns one of: "github", "gitlab", "bitbucket", "git" (fallback).
    """
    host = _extract_host(remote_url).lower()

    # If the host isn't a known forge, check if it's an SSH alias.
    if host and host not in ("github.com", "gitlab.com", "bitbucket.org"):
        resolved = _resolve_ssh_hostname(host, ssh_config_path=ssh_config_path)
        if resolved:
            host = resolved

    if host == "github.com":
        return "github"
    if host == "gitlab.com":
        return "gitlab"
    if host == "bitbucket.org":
        return "bitbucket"
    return "git"


# ---------------------------------------------------------------------------
# GitHub GraphQL provider
# ---------------------------------------------------------------------------

_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


def _resolve_github_token() -> str:
    """Resolve a GitHub token from environment or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    # Try gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    raise ValueError(
        "No GitHub token found. Set GITHUB_TOKEN or GH_TOKEN environment variable, "
        "or run `gh auth login`."
    )


def _parse_github_owner_repo(
    remote_url: str,
    *,
    ssh_config_path: Path | None = None,
) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub remote URL.

    Supports SSH host aliases: if the host in a ``git@<alias>:`` URL
    resolves to ``github.com`` via ``~/.ssh/config``, it is accepted.
    """
    # SSH: git@<host>:Owner/Repo.git
    ssh_match = re.match(r"git@([^:]+):([^/]+)/(.+?)(?:\.git)?$", remote_url)
    if ssh_match:
        host = ssh_match.group(1).lower()
        if host != "github.com":
            resolved = _resolve_ssh_hostname(
                host, ssh_config_path=ssh_config_path,
            )
            if resolved != "github.com":
                raise ValueError(
                    f"Cannot parse GitHub owner/repo from URL: {remote_url!r}"
                )
        return ssh_match.group(2), ssh_match.group(3)

    # HTTPS: https://github.com/Owner/Repo.git
    match = re.match(r"https?://github\.com/([^/]+)/(.+?)(?:\.git)?$", remote_url)
    if match:
        return match.group(1), match.group(2)

    raise ValueError(f"Cannot parse GitHub owner/repo from URL: {remote_url!r}")


class GitHubProvider:
    """Read-only access to a GitHub repository via the GraphQL API."""

    def __init__(
        self,
        remote_url: str,
        *,
        token: str | None = None,
        ssh_config_path: Path | None = None,
    ) -> None:
        self.remote_url = remote_url
        self.owner, self.repo = _parse_github_owner_repo(
            remote_url, ssh_config_path=ssh_config_path,
        )
        self.token = token or _resolve_github_token()

    def _graphql(
        self, query: str, variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query against the GitHub API."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        data = json.dumps(payload).encode()

        req = Request(
            _GITHUB_GRAPHQL_URL,
            data=data,
            headers={
                "Authorization": f"bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(req) as resp:
            return json.loads(resp.read())  # type: ignore[no-any-return]

    def list_files(self, path: str, ref: str = "HEAD") -> list[FileEntry]:
        """List files in a directory using the GraphQL Tree API."""
        expression = f"{ref}:{path}" if path else ref
        query = """
        query($owner: String!, $repo: String!, $expression: String!) {
          repository(owner: $owner, name: $repo) {
            object(expression: $expression) {
              ... on Tree {
                entries {
                  name
                  type
                }
              }
            }
          }
        }
        """
        result = self._graphql(query, {
            "owner": self.owner,
            "repo": self.repo,
            "expression": expression,
        })

        obj = result.get("data", {}).get("repository", {}).get("object")
        if obj is None:
            return []

        entries = obj.get("entries", [])
        prefix = f"{path}/" if path else ""
        return [
            FileEntry(
                name=e["name"],
                path=f"{prefix}{e['name']}",
                file_type=e["type"],
            )
            for e in entries
        ]

    def read_file(self, path: str, ref: str = "HEAD") -> str:
        """Read a single file's content."""
        result = self.read_files([path], ref=ref)
        if path not in result:
            raise FileNotFoundError(f"File not found in repo: {path}")
        return result[path]

    def read_files(self, paths: list[str], ref: str = "HEAD") -> dict[str, str]:
        """Batch-read multiple files using aliased GraphQL queries."""
        if not paths:
            return {}

        # Build aliased query fragments: one per file.
        fragments: list[str] = []
        alias_to_path: dict[str, str] = {}
        for i, file_path in enumerate(paths):
            alias = f"f{i}"
            alias_to_path[alias] = file_path
            expression = f"{ref}:{file_path}"
            fragments.append(
                f'{alias}: object(expression: "{expression}") '
                f"{{ ... on Blob {{ text byteSize }} }}"
            )

        query = (
            "query($owner: String!, $repo: String!) {\n"
            f"  repository(owner: $owner, name: $repo) {{\n"
            f"    {chr(10).join('    ' + f for f in fragments)}\n"
            f"  }}\n"
            "}"
        )

        result = self._graphql(query, {"owner": self.owner, "repo": self.repo})
        repo_data = result.get("data", {}).get("repository", {})

        contents: dict[str, str] = {}
        for alias, file_path in alias_to_path.items():
            entry = repo_data.get(alias)
            if entry is not None and isinstance(entry, dict):
                text = entry.get("text")
                if text is not None:
                    contents[file_path] = text

        return contents


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def provider_for_url(
    remote_url: str,
    *,
    token: str | None = None,
    ssh_config_path: Path | None = None,
) -> RemoteProvider:
    """Create the appropriate provider for a remote URL.

    Resolves SSH host aliases via ``~/.ssh/config`` so that URLs like
    ``git@github-personal:org/repo.git`` work when the alias maps to
    ``github.com``.

    Args:
        remote_url: Git remote URL (e.g. git@github.com:org/repo.git).
        token: Optional auth token. If omitted, resolved from environment.
        ssh_config_path: Override path to SSH config (for testing).

    Returns:
        A RemoteProvider instance.

    Raises:
        NotImplementedError: For forges not yet supported.
    """
    forge = detect_forge(remote_url, ssh_config_path=ssh_config_path)
    if forge == "github":
        return GitHubProvider(
            remote_url, token=token, ssh_config_path=ssh_config_path,
        )
    raise NotImplementedError(
        f"Remote read for {forge} forge is not yet supported. "
        f"URL: {remote_url}"
    )


# ---------------------------------------------------------------------------
# Pool resolution
# ---------------------------------------------------------------------------


@contextmanager
def resolve_pool_readonly(
    provider: RemoteProvider,
    subpath: str,
    *,
    ref: str = "HEAD",
) -> Iterator[Path]:
    """Fetch a remote pool's node files into an ephemeral tmpdir.

    Lists ``snapshots/`` and ``live/`` under the given subpath, reads all
    ``.md`` files via the provider, writes them to a temporary directory,
    and yields the pool root path. The tmpdir is cleaned up on exit.

    Args:
        provider: A RemoteProvider for the target repository.
        subpath: Path within the repo to the pool root (e.g. "registry/vehicles").
            Empty string means pool is at repo root.
        ref: Git ref to read from (default: HEAD).

    Yields:
        Path to the temporary pool directory containing snapshots/ and live/.
    """
    with tempfile.TemporaryDirectory(prefix="alph-ro-") as tmpdir:
        pool_root = Path(tmpdir) / "pool"

        # Always create both subdirs so downstream code finds the expected structure.
        (pool_root / "snapshots").mkdir(parents=True)
        (pool_root / "live").mkdir(parents=True)

        # List files in both subdirectories.
        all_md_paths: list[str] = []
        for subdir in ("snapshots", "live"):
            dir_path = f"{subpath}/{subdir}" if subpath else subdir
            entries = provider.list_files(dir_path, ref=ref)
            for entry in entries:
                if entry.name.endswith(".md"):
                    all_md_paths.append(entry.path)

        if all_md_paths:
            # Batch-read all .md file contents.
            contents = provider.read_files(all_md_paths, ref=ref)

            # Write each file to the local tmpdir.
            for file_path, text in contents.items():
                # Determine which subdir (snapshots or live) this belongs to.
                # Path may be "subpath/snapshots/file.md" or "snapshots/file.md".
                parts = Path(file_path).parts
                if "snapshots" in parts:
                    local_path = pool_root / "snapshots" / Path(file_path).name
                elif "live" in parts:
                    local_path = pool_root / "live" / Path(file_path).name
                else:
                    continue
                local_path.write_text(text)

        yield pool_root


# ---------------------------------------------------------------------------
# Clone management (RW mode)
# ---------------------------------------------------------------------------


def default_clone_dir(remote_url: str) -> Path:
    """Return the default cache directory for a remote registry clone.

    Uses ``~/.cache/alph/clones/<sha256(url)[:12]>/``.
    """
    url_hash = hashlib.sha256(remote_url.encode()).hexdigest()[:12]
    return Path.home() / ".cache" / "alph" / "clones" / url_hash


def _checkout_branch(clone_dir: Path, branch: str) -> None:
    """Check out a branch in a clone, fetching it first if needed.

    Handles shallow clones where the branch may not exist locally.
    Tries ``git checkout <branch>`` first; if that fails, fetches the
    branch and creates a local tracking branch from ``origin/<branch>``.
    """
    # Already on the right branch?
    head = subprocess.run(
        ["git", "-C", str(clone_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    if head.returncode == 0 and head.stdout.strip() == branch:
        return

    # Try simple checkout first (works if branch exists locally).
    simple = subprocess.run(
        ["git", "-C", str(clone_dir), "checkout", branch],
        capture_output=True, text=True, timeout=30,
    )
    if simple.returncode == 0:
        logger.debug("checked out branch: %s", branch)
        return

    # Branch not local — fetch and create a tracking branch.
    # Use --depth=1 to keep shallow clones shallow.
    fetch = subprocess.run(
        ["git", "-C", str(clone_dir), "fetch", "--depth", "1",
         "origin", branch],
        capture_output=True, text=True, timeout=60,
    )
    if fetch.returncode != 0:
        raise RuntimeError(
            f"git fetch origin {branch} failed: {fetch.stderr.strip()}"
        )

    # Try origin/<branch> first; fall back to FETCH_HEAD for shallow clones
    # where fetch doesn't create a remote tracking ref.
    result = subprocess.run(
        ["git", "-C", str(clone_dir), "checkout", "-b", branch,
         f"origin/{branch}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", "-b", branch,
             "FETCH_HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"git checkout -b {branch} failed: "
            f"{result.stderr.strip()}"
        )
    logger.debug("checked out branch: %s (from origin)", branch)


def _ssh_env(ssh_command: str) -> dict[str, str] | None:
    """Return an env dict with GIT_SSH_COMMAND set, or None if ssh_command is empty."""
    if not ssh_command:
        return None
    return {**os.environ, "GIT_SSH_COMMAND": ssh_command}


def clone_remote_registry(
    remote_url: str,
    clone_dir: Path,
    *,
    depth: int = 1,
    branch: str = "",
    ssh_command: str = "",
) -> bool:
    """Clone a remote git repository for RW access.

    If the clone directory already exists and contains a ``.git`` dir,
    ensures the requested branch is checked out but does not re-clone.

    Args:
        remote_url: Git remote URL.
        clone_dir: Local directory to clone into.
        depth: Clone depth (default 1 for shallow clone).
        branch: Git branch to check out. Empty string means default branch.
        ssh_command: Value for GIT_SSH_COMMAND env var. Empty means use system default.

    Returns:
        True if a new clone was created, False if one already existed.

    Raises:
        RuntimeError: If the clone or checkout fails.
    """
    if (clone_dir / ".git").is_dir():
        logger.debug("clone already exists: %s", clone_dir)
        if branch:
            _checkout_branch(clone_dir, branch)
        return False

    clone_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "clone", "--depth", str(depth)]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([remote_url, str(clone_dir)])
    logger.debug("cloning: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        env=_ssh_env(ssh_command),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return True


def pull_remote_registry(clone_dir: Path, *, ssh_command: str = "") -> None:
    """Pull latest changes in an existing clone.

    Args:
        clone_dir: Local clone directory.
        ssh_command: Value for GIT_SSH_COMMAND env var. Empty means use system default.

    Raises:
        RuntimeError: If the pull fails.
        FileNotFoundError: If clone_dir is not a git repo.
    """
    if not (clone_dir / ".git").is_dir():
        raise FileNotFoundError(
            f"Not a git repository: {clone_dir}"
        )
    logger.debug("pulling: %s", clone_dir)
    result = subprocess.run(
        ["git", "-C", str(clone_dir), "pull", "--rebase"],
        capture_output=True, text=True, timeout=60,
        env=_ssh_env(ssh_command),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git pull failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


def push_remote_registry(clone_dir: Path, *, ssh_command: str = "") -> None:
    """Push commits from a local clone to the remote.

    Args:
        clone_dir: Local clone directory.
        ssh_command: Value for GIT_SSH_COMMAND env var. Empty means use system default.

    Raises:
        RuntimeError: If the push fails.
    """
    if not (clone_dir / ".git").is_dir():
        raise FileNotFoundError(
            f"Not a git repository: {clone_dir}"
        )
    logger.debug("pushing: %s", clone_dir)
    result = subprocess.run(
        ["git", "-C", str(clone_dir), "push"],
        capture_output=True, text=True, timeout=60,
        env=_ssh_env(ssh_command),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git push failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
