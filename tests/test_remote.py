"""Behavior tests for remote registry providers and resolution."""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from alph.remote import (
    FileEntry,
    GitHubProvider,
    detect_forge,
    provider_for_url,
    resolve_pool_readonly,
)

# ---------------------------------------------------------------------------
# Forge detection
# ---------------------------------------------------------------------------


def test_detect_forge_github_ssh() -> None:
    """SSH git@ URL for github.com is detected as github."""
    assert detect_forge("git@github.com:AlpheusCEF/repo.git") == "github"


def test_detect_forge_github_https() -> None:
    """HTTPS URL for github.com is detected as github."""
    assert detect_forge("https://github.com/AlpheusCEF/repo.git") == "github"


def test_detect_forge_gitlab_ssh() -> None:
    """SSH URL for gitlab.com is detected as gitlab."""
    assert detect_forge("git@gitlab.com:org/repo.git") == "gitlab"


def test_detect_forge_gitlab_https() -> None:
    """HTTPS URL for gitlab.com is detected as gitlab."""
    assert detect_forge("https://gitlab.com/org/repo.git") == "gitlab"


def test_detect_forge_bitbucket_ssh() -> None:
    """SSH URL for bitbucket.org is detected as bitbucket."""
    assert detect_forge("git@bitbucket.org:org/repo.git") == "bitbucket"


def test_detect_forge_unknown_host() -> None:
    """Unknown host returns 'git' as fallback."""
    assert detect_forge("git@self-hosted.example.com:org/repo.git") == "git"


def test_detect_forge_github_enterprise() -> None:
    """Non-github.com host is not detected as github."""
    assert detect_forge("git@github.enterprise.corp:org/repo.git") == "git"


# ---------------------------------------------------------------------------
# provider_for_url
# ---------------------------------------------------------------------------


def test_provider_for_url_github_returns_github_provider() -> None:
    """GitHub URL returns a GitHubProvider."""
    provider = provider_for_url("git@github.com:org/repo.git", token="test-token")
    assert isinstance(provider, GitHubProvider)


def test_provider_for_url_unknown_raises() -> None:
    """Unknown forge raises NotImplementedError for now."""
    with pytest.raises(NotImplementedError, match="not yet supported"):
        provider_for_url("git@self-hosted.example.com:org/repo.git")


# ---------------------------------------------------------------------------
# GitHubProvider — owner/repo extraction
# ---------------------------------------------------------------------------


def test_github_provider_parses_ssh_url() -> None:
    """GitHubProvider extracts owner and repo from SSH URL."""
    p = GitHubProvider("git@github.com:AlpheusCEF/multi-pool-repo-example.git", token="t")
    assert p.owner == "AlpheusCEF"
    assert p.repo == "multi-pool-repo-example"


def test_github_provider_parses_https_url() -> None:
    """GitHubProvider extracts owner and repo from HTTPS URL."""
    p = GitHubProvider("https://github.com/AlpheusCEF/repo.git", token="t")
    assert p.owner == "AlpheusCEF"
    assert p.repo == "repo"


def test_github_provider_parses_https_no_dotgit() -> None:
    """GitHubProvider handles HTTPS URLs without .git suffix."""
    p = GitHubProvider("https://github.com/AlpheusCEF/repo", token="t")
    assert p.owner == "AlpheusCEF"
    assert p.repo == "repo"


def test_github_provider_parses_ssh_no_dotgit() -> None:
    """GitHubProvider handles SSH URLs without .git suffix."""
    p = GitHubProvider("git@github.com:Org/Repo", token="t")
    assert p.owner == "Org"
    assert p.repo == "Repo"


# ---------------------------------------------------------------------------
# GitHubProvider — list_files (mocked)
# ---------------------------------------------------------------------------


def _mock_graphql_tree_response(entries: list[dict[str, str]]) -> dict[str, Any]:
    """Build a mock GraphQL response for a tree listing."""
    return {
        "data": {
            "repository": {
                "object": {
                    "entries": entries,
                }
            }
        }
    }


def _make_mock_urlopen(response_data: dict[str, Any]) -> MagicMock:
    """Create a mock urlopen that returns response_data as JSON."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(response_data).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=mock_response)


def test_github_provider_list_files_returns_entries() -> None:
    """list_files returns FileEntry objects from GraphQL tree response."""
    response = _mock_graphql_tree_response([
        {"name": "abc123.md", "type": "blob"},
        {"name": "def456.md", "type": "blob"},
        {"name": "README", "type": "blob"},
    ])
    provider = GitHubProvider("git@github.com:org/repo.git", token="test-tok")

    with patch("alph.remote.urlopen", _make_mock_urlopen(response)):
        entries = provider.list_files("vehicles/snapshots")

    assert len(entries) == 3
    assert entries[0].name == "abc123.md"
    assert entries[0].path == "vehicles/snapshots/abc123.md"


def test_github_provider_list_files_empty_dir() -> None:
    """list_files returns empty list when directory has no entries."""
    response = _mock_graphql_tree_response([])
    provider = GitHubProvider("git@github.com:org/repo.git", token="test-tok")

    with patch("alph.remote.urlopen", _make_mock_urlopen(response)):
        entries = provider.list_files("vehicles/snapshots")

    assert entries == []


def test_github_provider_list_files_null_object() -> None:
    """list_files returns empty list when path does not exist (null object)."""
    response: dict[str, Any] = {"data": {"repository": {"object": None}}}
    provider = GitHubProvider("git@github.com:org/repo.git", token="test-tok")

    with patch("alph.remote.urlopen", _make_mock_urlopen(response)):
        entries = provider.list_files("nonexistent/path")

    assert entries == []


# ---------------------------------------------------------------------------
# GitHubProvider — read_files (mocked)
# ---------------------------------------------------------------------------


def _mock_graphql_batch_read_response(files: dict[str, str]) -> dict[str, Any]:
    """Build a mock GraphQL response for batch file reads.

    files: mapping of alias -> text content.
    """
    repo_data: dict[str, Any] = {}
    for alias, text in files.items():
        repo_data[alias] = {"text": text, "byteSize": len(text.encode())}
    return {"data": {"repository": repo_data}}


def test_github_provider_read_files_returns_contents() -> None:
    """read_files returns a dict mapping path -> file content."""
    response = _mock_graphql_batch_read_response({
        "f0": "---\nid: abc123\n---\nBody 1",
        "f1": "---\nid: def456\n---\nBody 2",
    })
    provider = GitHubProvider("git@github.com:org/repo.git", token="test-tok")
    paths = ["vehicles/snapshots/abc123.md", "vehicles/snapshots/def456.md"]

    with patch("alph.remote.urlopen", _make_mock_urlopen(response)):
        contents = provider.read_files(paths)

    assert len(contents) == 2
    assert contents["vehicles/snapshots/abc123.md"] == "---\nid: abc123\n---\nBody 1"
    assert contents["vehicles/snapshots/def456.md"] == "---\nid: def456\n---\nBody 2"


def test_github_provider_read_files_empty_list() -> None:
    """read_files with empty paths returns empty dict without API call."""
    provider = GitHubProvider("git@github.com:org/repo.git", token="test-tok")
    contents = provider.read_files([])
    assert contents == {}


def test_github_provider_read_files_null_entry() -> None:
    """read_files skips files that returned null (deleted/missing)."""
    response: dict[str, Any] = {"data": {"repository": {"f0": None, "f1": {"text": "content", "byteSize": 7}}}}
    provider = GitHubProvider("git@github.com:org/repo.git", token="test-tok")
    paths = ["a.md", "b.md"]

    with patch("alph.remote.urlopen", _make_mock_urlopen(response)):
        contents = provider.read_files(paths)

    assert len(contents) == 1
    assert contents["b.md"] == "content"


# ---------------------------------------------------------------------------
# GitHubProvider — read_file (single, mocked)
# ---------------------------------------------------------------------------


def test_github_provider_read_file_single() -> None:
    """read_file returns content of a single file."""
    response = _mock_graphql_batch_read_response({"f0": "file content here"})
    provider = GitHubProvider("git@github.com:org/repo.git", token="test-tok")

    with patch("alph.remote.urlopen", _make_mock_urlopen(response)):
        content = provider.read_file("path/to/file.md")

    assert content == "file content here"


def test_github_provider_read_file_not_found() -> None:
    """read_file raises FileNotFoundError when file does not exist."""
    response: dict[str, Any] = {"data": {"repository": {"f0": None}}}
    provider = GitHubProvider("git@github.com:org/repo.git", token="test-tok")

    with patch("alph.remote.urlopen", _make_mock_urlopen(response)), \
            pytest.raises(FileNotFoundError):
        provider.read_file("nonexistent.md")


# ---------------------------------------------------------------------------
# GitHubProvider — token resolution
# ---------------------------------------------------------------------------


def test_github_provider_uses_explicit_token() -> None:
    """Explicit token is used over environment."""
    p = GitHubProvider("git@github.com:org/repo.git", token="explicit-tok")
    assert p.token == "explicit-tok"


def test_github_provider_reads_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to GITHUB_TOKEN env var."""
    monkeypatch.setenv("GITHUB_TOKEN", "env-tok")
    p = GitHubProvider("git@github.com:org/repo.git")
    assert p.token == "env-tok"


def test_github_provider_reads_gh_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to GH_TOKEN env var."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-tok")
    p = GitHubProvider("git@github.com:org/repo.git")
    assert p.token == "gh-tok"


def test_github_provider_no_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token available raises ValueError."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # Also mock subprocess to prevent gh auth token from working
    with patch("alph.remote.subprocess.run", side_effect=FileNotFoundError), \
            pytest.raises(ValueError, match="No GitHub token"):
        GitHubProvider("git@github.com:org/repo.git")


def test_github_provider_falls_back_to_gh_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to `gh auth token` CLI when env vars are unset."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "gh-cli-token\n"
    with patch("alph.remote.subprocess.run", return_value=mock_result):
        p = GitHubProvider("git@github.com:org/repo.git")
    assert p.token == "gh-cli-token"


# ---------------------------------------------------------------------------
# resolve_pool_readonly
# ---------------------------------------------------------------------------


def test_resolve_pool_readonly_creates_tmpdir_with_files() -> None:
    """resolve_pool_readonly fetches files and writes them to a temp directory."""
    mock_provider = MagicMock()
    mock_provider.list_files.side_effect = [
        # snapshots/
        [FileEntry(name="abc123.md", path="pool/snapshots/abc123.md", file_type="blob")],
        # live/
        [FileEntry(name="def456.md", path="pool/live/def456.md", file_type="blob")],
    ]
    mock_provider.read_files.return_value = {
        "pool/snapshots/abc123.md": "---\nid: abc123\n---\nSnapshot body",
        "pool/live/def456.md": "---\nid: def456\n---\nLive body",
    }

    with resolve_pool_readonly(mock_provider, "pool") as pool_path:
        assert (pool_path / "snapshots" / "abc123.md").exists()
        assert (pool_path / "live" / "def456.md").exists()
        assert "abc123" in (pool_path / "snapshots" / "abc123.md").read_text()
        assert "def456" in (pool_path / "live" / "def456.md").read_text()


def test_resolve_pool_readonly_empty_pool() -> None:
    """resolve_pool_readonly handles a pool with no nodes."""
    mock_provider = MagicMock()
    mock_provider.list_files.return_value = []
    mock_provider.read_files.return_value = {}

    with resolve_pool_readonly(mock_provider, "pool") as pool_path:
        assert (pool_path / "snapshots").is_dir()
        assert (pool_path / "live").is_dir()
        assert list(pool_path.glob("**/*.md")) == []


def test_resolve_pool_readonly_filters_md_only() -> None:
    """resolve_pool_readonly only fetches .md files."""
    mock_provider = MagicMock()
    mock_provider.list_files.side_effect = [
        [
            FileEntry(name="abc123.md", path="p/snapshots/abc123.md", file_type="blob"),
            FileEntry(name=".gitkeep", path="p/snapshots/.gitkeep", file_type="blob"),
            FileEntry(name="notes.txt", path="p/snapshots/notes.txt", file_type="blob"),
        ],
        [],  # live/ empty
    ]
    mock_provider.read_files.return_value = {
        "p/snapshots/abc123.md": "---\nid: abc123\n---\n",
    }

    with resolve_pool_readonly(mock_provider, "p") as pool_path:
        files = list(pool_path.glob("**/*"))
        md_files = [f for f in files if f.is_file()]
        assert len(md_files) == 1
        assert md_files[0].name == "abc123.md"


def test_resolve_pool_readonly_cleanup() -> None:
    """resolve_pool_readonly cleans up the tmpdir after exiting context."""
    mock_provider = MagicMock()
    mock_provider.list_files.return_value = []
    mock_provider.read_files.return_value = {}

    with resolve_pool_readonly(mock_provider, "pool") as pool_path:
        tmpdir = pool_path.parent
        assert tmpdir.exists()

    assert not tmpdir.exists()


def test_resolve_pool_readonly_root_subpath() -> None:
    """resolve_pool_readonly works when subpath is empty (pool at repo root)."""
    mock_provider = MagicMock()
    mock_provider.list_files.side_effect = [
        [FileEntry(name="a.md", path="snapshots/a.md", file_type="blob")],
        [],
    ]
    mock_provider.read_files.return_value = {
        "snapshots/a.md": "---\nid: aaa\n---\n",
    }

    with resolve_pool_readonly(mock_provider, "") as pool_path:
        assert (pool_path / "snapshots" / "a.md").exists()
