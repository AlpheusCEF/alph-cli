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
# Forge detection
# ---------------------------------------------------------------------------


def detect_forge(remote_url: str) -> str:
    """Detect the git forge from a remote URL.

    Returns one of: "github", "gitlab", "bitbucket", "git" (fallback).
    """
    # Normalize: extract the host from SSH or HTTPS URLs.
    host = ""
    if remote_url.startswith("git@"):
        # git@host:org/repo.git
        match = re.match(r"git@([^:]+):", remote_url)
        if match:
            host = match.group(1)
    elif "://" in remote_url:
        # https://host/... or ssh://git@host/...
        match = re.match(r"[a-z+]+://(?:[^@]+@)?([^/]+)", remote_url)
        if match:
            host = match.group(1)

    host = host.lower()
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


def _parse_github_owner_repo(remote_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub remote URL."""
    # SSH: git@github.com:Owner/Repo.git
    match = re.match(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?$", remote_url)
    if match:
        return match.group(1), match.group(2)

    # HTTPS: https://github.com/Owner/Repo.git
    match = re.match(r"https?://github\.com/([^/]+)/(.+?)(?:\.git)?$", remote_url)
    if match:
        return match.group(1), match.group(2)

    raise ValueError(f"Cannot parse GitHub owner/repo from URL: {remote_url!r}")


class GitHubProvider:
    """Read-only access to a GitHub repository via the GraphQL API."""

    def __init__(self, remote_url: str, *, token: str | None = None) -> None:
        self.remote_url = remote_url
        self.owner, self.repo = _parse_github_owner_repo(remote_url)
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


def provider_for_url(remote_url: str, *, token: str | None = None) -> RemoteProvider:
    """Create the appropriate provider for a remote URL.

    Args:
        remote_url: Git remote URL (e.g. git@github.com:org/repo.git).
        token: Optional auth token. If omitted, resolved from environment.

    Returns:
        A RemoteProvider instance.

    Raises:
        NotImplementedError: For forges not yet supported.
    """
    forge = detect_forge(remote_url)
    if forge == "github":
        return GitHubProvider(remote_url, token=token)
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
    fetch = subprocess.run(
        ["git", "-C", str(clone_dir), "fetch", "origin", branch],
        capture_output=True, text=True, timeout=60,
    )
    if fetch.returncode != 0:
        raise RuntimeError(
            f"git fetch origin {branch} failed: {fetch.stderr.strip()}"
        )

    result = subprocess.run(
        ["git", "-C", str(clone_dir), "checkout", "-b", branch,
         f"origin/{branch}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git checkout -b {branch} origin/{branch} failed: "
            f"{result.stderr.strip()}"
        )
    logger.debug("checked out branch: %s (from origin)", branch)


def clone_remote_registry(
    remote_url: str,
    clone_dir: Path,
    *,
    depth: int = 1,
    branch: str = "",
) -> bool:
    """Clone a remote git repository for RW access.

    If the clone directory already exists and contains a ``.git`` dir,
    ensures the requested branch is checked out but does not re-clone.

    Args:
        remote_url: Git remote URL.
        clone_dir: Local directory to clone into.
        depth: Clone depth (default 1 for shallow clone).
        branch: Git branch to check out. Empty string means default branch.

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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return True


def pull_remote_registry(clone_dir: Path) -> None:
    """Pull latest changes in an existing clone.

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
        ["git", "-C", str(clone_dir), "pull", "--ff-only"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git pull failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


def push_remote_registry(clone_dir: Path) -> None:
    """Push commits from a local clone to the remote.

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
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git push failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
