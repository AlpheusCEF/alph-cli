"""Behavior tests for remote registry providers and resolution."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from alph.remote import (
    FileEntry,
    GitHubProvider,
    _resolve_ssh_hostname,
    clone_remote_registry,
    default_clone_dir,
    detect_forge,
    provider_for_url,
    pull_remote_registry,
    push_remote_registry,
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
# SSH host alias resolution
# ---------------------------------------------------------------------------


def test_resolve_ssh_hostname_returns_hostname_for_alias(tmp_path: Path) -> None:
    """_resolve_ssh_hostname returns the HostName for a matching Host alias."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host github-personal\n"
        "    HostName github.com\n"
        "    User git\n"
        "    IdentityFile ~/.ssh/my_key\n"
    )
    assert _resolve_ssh_hostname("github-personal", ssh_config_path=ssh_config) == "github.com"


def test_resolve_ssh_hostname_returns_none_for_unknown(tmp_path: Path) -> None:
    """_resolve_ssh_hostname returns None when alias is not in config."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host github-personal\n"
        "    HostName github.com\n"
    )
    assert _resolve_ssh_hostname("unknown-host", ssh_config_path=ssh_config) is None


def test_resolve_ssh_hostname_returns_none_when_no_ssh_config(tmp_path: Path) -> None:
    """_resolve_ssh_hostname returns None when ssh config does not exist."""
    assert _resolve_ssh_hostname("anything", ssh_config_path=tmp_path / "nonexistent") is None


def test_resolve_ssh_hostname_handles_multiple_hosts(tmp_path: Path) -> None:
    """_resolve_ssh_hostname picks the correct host from multiple entries."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host work-github\n"
        "    HostName github.enterprise.corp\n"
        "    User git\n"
        "\n"
        "Host personal-github\n"
        "    HostName github.com\n"
        "    User git\n"
        "\n"
        "Host gitlab-work\n"
        "    HostName gitlab.com\n"
        "    User git\n"
    )
    assert _resolve_ssh_hostname("work-github", ssh_config_path=ssh_config) == "github.enterprise.corp"
    assert _resolve_ssh_hostname("personal-github", ssh_config_path=ssh_config) == "github.com"
    assert _resolve_ssh_hostname("gitlab-work", ssh_config_path=ssh_config) == "gitlab.com"


def test_resolve_ssh_hostname_ignores_wildcard_hosts(tmp_path: Path) -> None:
    """_resolve_ssh_hostname does not match wildcard Host entries."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host *\n"
        "    HostName default.example.com\n"
        "\n"
        "Host github-personal\n"
        "    HostName github.com\n"
    )
    assert _resolve_ssh_hostname("github-personal", ssh_config_path=ssh_config) == "github.com"
    assert _resolve_ssh_hostname("random-host", ssh_config_path=ssh_config) is None


def test_resolve_ssh_hostname_case_insensitive_keyword(tmp_path: Path) -> None:
    """SSH config keywords are case-insensitive per spec."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "host github-personal\n"
        "    hostname github.com\n"
    )
    assert _resolve_ssh_hostname("github-personal", ssh_config_path=ssh_config) == "github.com"


def test_detect_forge_resolves_ssh_alias(tmp_path: Path) -> None:
    """detect_forge resolves SSH host aliases to detect the forge."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host github-personal\n"
        "    HostName github.com\n"
    )
    assert detect_forge("git@github-personal:org/repo.git", ssh_config_path=ssh_config) == "github"


def test_detect_forge_resolves_gitlab_alias(tmp_path: Path) -> None:
    """detect_forge resolves SSH alias pointing to gitlab.com."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host my-gitlab\n"
        "    HostName gitlab.com\n"
    )
    assert detect_forge("git@my-gitlab:org/repo.git", ssh_config_path=ssh_config) == "gitlab"


def test_provider_for_url_resolves_ssh_alias(tmp_path: Path) -> None:
    """provider_for_url creates GitHubProvider for an SSH alias pointing to github.com."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host gh-personal\n"
        "    HostName github.com\n"
    )
    provider = provider_for_url(
        "git@gh-personal:AlpheusCEF/repo.git",
        token="test-token",
        ssh_config_path=ssh_config,
    )
    assert isinstance(provider, GitHubProvider)
    assert provider.owner == "AlpheusCEF"
    assert provider.repo == "repo"


def test_github_provider_parses_ssh_alias_url(tmp_path: Path) -> None:
    """GitHubProvider extracts owner/repo from SSH alias URL."""
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host gh-personal\n"
        "    HostName github.com\n"
    )
    p = GitHubProvider(
        "git@gh-personal:AlpheusCEF/multi-pool-repo-example.git",
        token="t",
        ssh_config_path=ssh_config,
    )
    assert p.owner == "AlpheusCEF"
    assert p.repo == "multi-pool-repo-example"


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


# ---------------------------------------------------------------------------
# default_clone_dir
# ---------------------------------------------------------------------------


def test_default_clone_dir_uses_sha256_prefix() -> None:
    """default_clone_dir returns ~/.cache/alph/clones/<sha256[:12]>."""
    url = "git@github.com:org/repo.git"
    result = default_clone_dir(url)
    import hashlib
    expected_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    assert result == Path.home() / ".cache" / "alph" / "clones" / expected_hash


def test_default_clone_dir_different_urls_produce_different_dirs() -> None:
    """Different URLs produce different clone directories."""
    dir1 = default_clone_dir("git@github.com:org/repo1.git")
    dir2 = default_clone_dir("git@github.com:org/repo2.git")
    assert dir1 != dir2


# ---------------------------------------------------------------------------
# clone_remote_registry
# ---------------------------------------------------------------------------


def test_clone_remote_registry_runs_git_clone(tmp_path: Path) -> None:
    """clone_remote_registry calls git clone with correct args."""
    clone_dir = tmp_path / "clone"
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        result = clone_remote_registry("git@github.com:org/repo.git", clone_dir)
    assert result is True
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "git"
    assert call_args[1] == "clone"
    assert "--depth" in call_args
    assert "git@github.com:org/repo.git" in call_args


def test_clone_remote_registry_passes_ssh_command_as_env(tmp_path: Path) -> None:
    """clone_remote_registry sets GIT_SSH_COMMAND in env when ssh_command is given."""
    clone_dir = tmp_path / "clone"
    mock_result = MagicMock()
    mock_result.returncode = 0
    ssh_cmd = "ssh -i /Users/cpettet/.ssh/github_chasemp.pri -o IdentitiesOnly=yes"
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        clone_remote_registry("git@github.com:org/repo.git", clone_dir, ssh_command=ssh_cmd)
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs.get("env", {}).get("GIT_SSH_COMMAND") == ssh_cmd


def test_clone_remote_registry_skips_existing(tmp_path: Path) -> None:
    """clone_remote_registry is a no-op when .git already exists."""
    clone_dir = tmp_path / "clone"
    (clone_dir / ".git").mkdir(parents=True)
    with patch("alph.remote.subprocess.run") as mock_run:
        result = clone_remote_registry("git@github.com:org/repo.git", clone_dir)
    mock_run.assert_not_called()
    assert result is False


def test_clone_remote_registry_raises_on_failure(tmp_path: Path) -> None:
    """clone_remote_registry raises RuntimeError on git failure."""
    clone_dir = tmp_path / "clone"
    mock_result = MagicMock()
    mock_result.returncode = 128
    mock_result.stderr = "fatal: repository not found"
    with patch("alph.remote.subprocess.run", return_value=mock_result), \
            pytest.raises(RuntimeError, match="git clone failed"):
        clone_remote_registry("git@github.com:org/repo.git", clone_dir)


def test_clone_remote_registry_custom_depth(tmp_path: Path) -> None:
    """clone_remote_registry passes custom depth to git clone."""
    clone_dir = tmp_path / "clone"
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        clone_remote_registry("git@github.com:org/repo.git", clone_dir, depth=5)
    call_args = mock_run.call_args[0][0]
    depth_idx = call_args.index("--depth")
    assert call_args[depth_idx + 1] == "5"


def test_clone_remote_registry_passes_branch_to_git_clone(tmp_path: Path) -> None:
    """clone_remote_registry passes --branch to git clone when specified."""
    clone_dir = tmp_path / "clone"
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        result = clone_remote_registry(
            "git@github.com:org/repo.git", clone_dir, branch="seeded",
        )
    assert result is True
    # Single git clone call with --branch seeded.
    assert mock_run.call_count == 1
    clone_cmd = mock_run.call_args_list[0][0][0]
    assert clone_cmd[1] == "clone"
    assert "--branch" in clone_cmd
    assert "seeded" in clone_cmd


def test_clone_remote_registry_checks_out_branch_on_existing(tmp_path: Path) -> None:
    """clone_remote_registry checks out branch on existing clone if not on it."""
    clone_dir = tmp_path / "clone"
    (clone_dir / ".git").mkdir(parents=True)

    def _side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
        r = MagicMock()
        r.returncode = 0
        r.stdout = "main"
        r.stderr = ""
        # Simulate: rev-parse returns "main", simple checkout succeeds.
        if "checkout" in cmd and "-b" not in cmd and cmd[-1] == "seeded":
            r.returncode = 0  # simple checkout works
        return r

    with patch("alph.remote.subprocess.run", side_effect=_side_effect) as mock_run:
        result = clone_remote_registry(
            "git@github.com:org/repo.git", clone_dir, branch="seeded",
        )
    assert result is False
    # Should have: rev-parse, checkout (no clone)
    cmds = [call[0][0] for call in mock_run.call_args_list]
    assert not any(c[1] == "clone" for c in cmds if len(c) > 1)
    assert any("checkout" in cmd for cmd in cmds)


def test_clone_remote_registry_skips_checkout_if_already_on_branch(tmp_path: Path) -> None:
    """clone_remote_registry skips checkout when already on the requested branch."""
    clone_dir = tmp_path / "clone"
    (clone_dir / ".git").mkdir(parents=True)
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "seeded"  # already on seeded
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        result = clone_remote_registry(
            "git@github.com:org/repo.git", clone_dir, branch="seeded",
        )
    assert result is False
    # Only rev-parse, no fetch or checkout
    assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# pull_remote_registry
# ---------------------------------------------------------------------------


def test_pull_remote_registry_runs_git_pull(tmp_path: Path) -> None:
    """pull_remote_registry calls git pull --rebase to handle diverged branches."""
    (tmp_path / ".git").mkdir()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        pull_remote_registry(tmp_path)
    call_args = mock_run.call_args[0][0]
    assert "pull" in call_args
    assert "--rebase" in call_args
    assert "--ff-only" not in call_args


def test_pull_remote_registry_passes_ssh_command_as_env(tmp_path: Path) -> None:
    """pull_remote_registry sets GIT_SSH_COMMAND in env when ssh_command is given."""
    (tmp_path / ".git").mkdir()
    mock_result = MagicMock()
    mock_result.returncode = 0
    ssh_cmd = "ssh -i /Users/cpettet/.ssh/github_chasemp.pri -o IdentitiesOnly=yes"
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        pull_remote_registry(tmp_path, ssh_command=ssh_cmd)
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs.get("env", {}).get("GIT_SSH_COMMAND") == ssh_cmd


def test_pull_remote_registry_no_ssh_command_by_default(tmp_path: Path) -> None:
    """pull_remote_registry does not inject GIT_SSH_COMMAND when ssh_command is empty."""
    (tmp_path / ".git").mkdir()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        pull_remote_registry(tmp_path)
    call_kwargs = mock_run.call_args[1]
    env = call_kwargs.get("env")
    assert env is None or "GIT_SSH_COMMAND" not in env


def test_pull_remote_registry_raises_on_not_git_repo(tmp_path: Path) -> None:
    """pull_remote_registry raises FileNotFoundError for non-git dir."""
    with pytest.raises(FileNotFoundError, match="Not a git repository"):
        pull_remote_registry(tmp_path)


def test_pull_remote_registry_raises_on_failure(tmp_path: Path) -> None:
    """pull_remote_registry raises RuntimeError on git failure."""
    (tmp_path / ".git").mkdir()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "merge conflict"
    with patch("alph.remote.subprocess.run", return_value=mock_result), \
            pytest.raises(RuntimeError, match="git pull failed"):
        pull_remote_registry(tmp_path)


# ---------------------------------------------------------------------------
# push_remote_registry
# ---------------------------------------------------------------------------


def test_push_remote_registry_runs_git_push(tmp_path: Path) -> None:
    """push_remote_registry calls git push."""
    (tmp_path / ".git").mkdir()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        push_remote_registry(tmp_path)
    call_args = mock_run.call_args[0][0]
    assert "push" in call_args


def test_push_remote_registry_passes_ssh_command_as_env(tmp_path: Path) -> None:
    """push_remote_registry sets GIT_SSH_COMMAND in env when ssh_command is given."""
    (tmp_path / ".git").mkdir()
    mock_result = MagicMock()
    mock_result.returncode = 0
    ssh_cmd = "ssh -i /Users/cpettet/.ssh/github_chasemp.pri -o IdentitiesOnly=yes"
    with patch("alph.remote.subprocess.run", return_value=mock_result) as mock_run:
        push_remote_registry(tmp_path, ssh_command=ssh_cmd)
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs.get("env", {}).get("GIT_SSH_COMMAND") == ssh_cmd


def test_push_remote_registry_raises_on_not_git_repo(tmp_path: Path) -> None:
    """push_remote_registry raises FileNotFoundError for non-git dir."""
    with pytest.raises(FileNotFoundError, match="Not a git repository"):
        push_remote_registry(tmp_path)


def test_push_remote_registry_raises_on_failure(tmp_path: Path) -> None:
    """push_remote_registry raises RuntimeError on git failure."""
    (tmp_path / ".git").mkdir()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "permission denied"
    with patch("alph.remote.subprocess.run", return_value=mock_result), \
            pytest.raises(RuntimeError, match="git push failed"):
        push_remote_registry(tmp_path)
