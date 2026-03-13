#!/usr/bin/env bash
# human_test.sh — Interactive walkthrough of content_type + update_node features
#
# Runs real alph commands in a temporary environment. Each step explains what
# it does, shows the command, waits for confirmation, runs it, and validates
# the output. Nothing touches your real config or pools.
#
# Usage:
#   cd alph-cli/
#   bash tests/human_test.sh
#
# The script auto-detects whether to use 'poetry run alph' or bare 'alph'.
# If running from the alph-cli repo with unreleased changes, poetry run is
# preferred so you test the development version.

set -euo pipefail

# ---------------------------------------------------------------------------
# Detect alph invocation
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$REPO_DIR/pyproject.toml" ]] && command -v poetry &>/dev/null; then
    ALPH="poetry -C $REPO_DIR run alph"
    ALPH_LABEL="poetry run alph (dev)"
elif command -v alph &>/dev/null; then
    ALPH="alph"
    ALPH_LABEL="alph (installed)"
else
    echo "Error: alph not found. Run from alph-cli/ with poetry, or install alph."
    exit 1
fi

# ---------------------------------------------------------------------------
# Colors and formatting
# ---------------------------------------------------------------------------

BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
RESET='\033[0m'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_section_num=0

section() {
    _section_num=$((_section_num + 1))
    echo ""
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${BOLD}${BLUE}  Section ${_section_num}: $1${RESET}"
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
}

explain() {
    echo -e "${CYAN}$1${RESET}"
}

show_cmd() {
    echo ""
    echo -e "  ${BOLD}\$ $1${RESET}"
    echo ""
}

confirm() {
    echo -en "${DIM}Press Enter to run (or 'q' to quit)...${RESET} "
    read -r response
    if [[ "$response" == "q" || "$response" == "Q" ]]; then
        echo -e "${YELLOW}Exiting early.${RESET}"
        exit 0
    fi
}

run_cmd() {
    # Run a command, display output, and capture it in $LAST_OUTPUT.
    # Substitutes 'alph ' at the start with $ALPH for actual execution.
    local cmd="$1"
    local real_cmd="${cmd/#alph /$ALPH }"
    LAST_EXIT=0
    LAST_OUTPUT=$(eval "$real_cmd" 2>&1) || LAST_EXIT=$?
    echo -e "${DIM}─── output ───${RESET}"
    echo "$LAST_OUTPUT"
    echo -e "${DIM}─── exit: ${LAST_EXIT} ───${RESET}"
    echo ""
}

step() {
    # Full step: explain, show command, confirm, run.
    # Commands are written as 'alph ...' and translated to $ALPH at runtime.
    local description="$1"
    local cmd="$2"
    explain "$description"
    show_cmd "$cmd"
    confirm
    run_cmd "$cmd"
}

check_pass() {
    echo -e "  ${GREEN}PASS${RESET}: $1"
}

check_fail() {
    echo -e "  ${RED}FAIL${RESET}: $1"
    FAILURES=$((FAILURES + 1))
}

check_contains() {
    local label="$1"
    local needle="$2"
    if echo "$LAST_OUTPUT" | grep -q "$needle"; then
        check_pass "$label"
    else
        check_fail "$label — expected to find '$needle' in output"
    fi
}

check_not_contains() {
    local label="$1"
    local needle="$2"
    if echo "$LAST_OUTPUT" | grep -q "$needle"; then
        check_fail "$label — did NOT expect '$needle' in output"
    else
        check_pass "$label"
    fi
}

check_exit() {
    local expected="$1"
    local label="$2"
    if [[ "$LAST_EXIT" -eq "$expected" ]]; then
        check_pass "$label (exit $expected)"
    else
        check_fail "$label — expected exit $expected, got $LAST_EXIT"
    fi
}

pause_between_sections() {
    echo ""
    echo -en "${YELLOW}Continue to next section? (Enter=yes, q=quit)${RESET} "
    read -r response
    if [[ "$response" == "q" || "$response" == "Q" ]]; then
        echo -e "${YELLOW}Exiting early.${RESET}"
        exit 0
    fi
}

# Extract node ID from "node created: <id>" output
extract_node_id() {
    echo "$LAST_OUTPUT" | grep -oE '[a-f0-9]{12}' | head -1
}

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

FAILURES=0
LAST_OUTPUT=""
LAST_EXIT=0
TEST_DIR=$(mktemp -d)
POOL_DIR=""
ORIG_ALPH_CONFIG_DIR="${ALPH_CONFIG_DIR:-}"

cleanup() {
    if [[ -n "$TEST_DIR" && -d "$TEST_DIR" ]]; then
        rm -rf "$TEST_DIR"
    fi
    if [[ -n "$ORIG_ALPH_CONFIG_DIR" ]]; then
        export ALPH_CONFIG_DIR="$ORIG_ALPH_CONFIG_DIR"
    else
        unset ALPH_CONFIG_DIR 2>/dev/null || true
    fi
}
trap cleanup EXIT

export ALPH_CONFIG_DIR="$TEST_DIR/global"

echo -e "${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════════════════════╗"
echo "  ║     AlpheusCEF — Interactive Feature Walkthrough            ║"
echo "  ║     content_type + update_node + task type                  ║"
echo "  ╚══════════════════════════════════════════════════════════════╝"
echo -e "${RESET}"
echo -e "This script walks through the new features added in this release:"
echo -e "  1. ${BOLD}task${RESET} content type"
echo -e "  2. ${BOLD}--tags${RESET}, ${BOLD}--meta${RESET}, ${BOLD}--related-to${RESET} flags on alph add"
echo -e "  3. ${BOLD}alph update${RESET} command for modifying existing nodes"
echo -e "  4. MCP tool_update_node (tested indirectly via CLI)"
echo ""
echo -e "Using:          ${DIM}$ALPH_LABEL${RESET}"
echo -e "Temp directory: ${DIM}$TEST_DIR${RESET}"
echo -e "Config dir:     ${DIM}$ALPH_CONFIG_DIR${RESET}"
echo ""
echo -e "${DIM}Each step shows a command, waits for you to press Enter, runs it,"
echo -e "then validates the output. Press 'q' at any prompt to exit early.${RESET}"

# ═══════════════════════════════════════════════════════════════════════════
section "Environment Setup"
# ═══════════════════════════════════════════════════════════════════════════

explain "Create an isolated registry and pool for testing."
explain "This uses a temp directory so nothing touches your real config."

step "Initialize a test registry." \
    "alph registry init --pool-home '$TEST_DIR/registry' --id test-reg --context 'Human test registry'"
check_exit 0 "registry init succeeds"
check_contains "registry created" "test-reg"

step "Initialize a pool inside the registry." \
    "alph pool init --registry test-reg --name test-pool --context 'Feature testing pool' --cwd '$TEST_DIR/registry'"
check_exit 0 "pool init succeeds"

POOL_DIR="$TEST_DIR/registry/test-pool"
explain "Pool created at: $POOL_DIR"
echo ""

pause_between_sections

# ═══════════════════════════════════════════════════════════════════════════
section "Task Content Type"
# ═══════════════════════════════════════════════════════════════════════════

explain "The 'task' content type is new. It has no required meta fields,"
explain "making it flexible for task management (fin-cli integration)."

step "Create a task node with --ct task." \
    "alph add -c 'Fix login bug' --ct task --pool '$POOL_DIR' --creator tester@example.com"
check_exit 0 "task node created"
NODE_TASK=$(extract_node_id)
echo -e "  ${DIM}Captured node ID: $NODE_TASK${RESET}"

step "Create a task with optional meta (priority, due date)." \
    "alph add -c 'Write quarterly report' --ct task --meta priority=high --meta due=2026-04-01 --tags urgent --tags work --pool '$POOL_DIR' --creator tester@example.com"
check_exit 0 "task with meta created"
NODE_TASK_META=$(extract_node_id)
echo -e "  ${DIM}Captured node ID: $NODE_TASK_META${RESET}"

step "Show the task node to verify frontmatter." \
    "alph show '$NODE_TASK_META' --pool '$POOL_DIR'"
check_exit 0 "show succeeds"
check_contains "content_type is task" "task"
check_contains "priority meta present" "priority"
check_contains "due meta present" "due"
check_contains "urgent tag present" "urgent"
check_contains "work tag present" "work"

step "Validate the pool to confirm task nodes are schema-compliant." \
    "alph validate --pool '$POOL_DIR'"
check_exit 0 "validation passes with task nodes"
check_contains "pool is valid" "valid"

pause_between_sections

# ═══════════════════════════════════════════════════════════════════════════
section "CLI --tags, --meta, and --related-to Flags"
# ═══════════════════════════════════════════════════════════════════════════

explain "These flags were previously only available via core.py."
explain "Now they are wired into the CLI's 'add' command."

step "Create a gdoc node with --meta url=... (required for gdoc type)." \
    "alph add -c 'Auth design doc' --ct gdoc --meta url=https://docs.google.com/doc/d/abc --tags architecture --pool '$POOL_DIR' --creator tester@example.com"
check_exit 0 "gdoc with meta created"
NODE_GDOC=$(extract_node_id)

step "Create a node with --related-to referencing the gdoc." \
    "alph add -c 'Auth implementation ticket' --related-to '$NODE_GDOC' --pool '$POOL_DIR' --creator tester@example.com"
check_exit 0 "node with related_to created"
NODE_RELATED=$(extract_node_id)

step "Show the related node to verify the cross-reference." \
    "alph show '$NODE_RELATED' --pool '$POOL_DIR'"
check_exit 0 "show related node"
check_contains "related_to contains gdoc ID" "$NODE_GDOC"

pause_between_sections

# ═══════════════════════════════════════════════════════════════════════════
section "Input Validation (Error Cases)"
# ═══════════════════════════════════════════════════════════════════════════

explain "The CLI should reject invalid inputs with clear error messages."
explain "These commands are expected to fail."

step "Reject unknown content_type." \
    "alph add -c 'bad type' --ct foobar --pool '$POOL_DIR' --creator tester@example.com || true"
check_contains "error mentions content_type" "content_type"

step "Reject malformed --meta (missing '=')." \
    "alph add -c 'bad meta' --meta no-equals --pool '$POOL_DIR' --creator tester@example.com || true"
check_contains "error mentions key=value" "key=value"

step "Reject unknown content_type on update too." \
    "alph update '$NODE_TASK' --ct foobar --pool '$POOL_DIR' || true"
check_contains "update rejects bad content_type" "content_type"

pause_between_sections

# ═══════════════════════════════════════════════════════════════════════════
section "Update Node — Status Changes"
# ═══════════════════════════════════════════════════════════════════════════

explain "alph update modifies an existing node's frontmatter or body."
explain "This is a new core capability required for fin-cli Phase B."

step "List active nodes (task should be visible)." \
    "alph list --pool '$POOL_DIR'"
check_exit 0 "list succeeds"
check_contains "task node visible" "Fix login bug"

step "Archive the task node." \
    "alph update '$NODE_TASK' --status archived --pool '$POOL_DIR'"
check_exit 0 "update status succeeds"
check_contains "update confirmed" "updated"

step "List active nodes again (archived task should be gone)." \
    "alph list --pool '$POOL_DIR'"
check_exit 0 "list after archive"
check_not_contains "archived task hidden from active list" "Fix login bug"

step "List archived nodes to confirm it moved." \
    "alph list --pool '$POOL_DIR' -s archived"
check_exit 0 "list archived"
check_contains "archived task visible with -s archived" "Fix login bug"

pause_between_sections

# ═══════════════════════════════════════════════════════════════════════════
section "Update Node — Tags and Meta"
# ═══════════════════════════════════════════════════════════════════════════

explain "Tags can be added/removed incrementally. Meta merges into existing values."

step "Add a 'done' tag to the archived task." \
    "alph update '$NODE_TASK' --tags-add done --pool '$POOL_DIR'"
check_exit 0 "tags-add succeeds"

step "Show the node to verify the tag was added." \
    "alph show '$NODE_TASK' --pool '$POOL_DIR'"
check_exit 0 "show after tag add"
check_contains "'done' tag present" "done"

step "Add meta to the quarterly report task." \
    "alph update '$NODE_TASK_META' --meta assignee=chase --pool '$POOL_DIR'"
check_exit 0 "meta merge succeeds"

step "Show the node to verify meta was merged (not replaced)." \
    "alph show '$NODE_TASK_META' --pool '$POOL_DIR'"
check_exit 0 "show after meta merge"
check_contains "original priority still present" "priority"
check_contains "new assignee present" "assignee"

step "Remove the 'urgent' tag from the report task." \
    "alph update '$NODE_TASK_META' --tags-remove urgent --pool '$POOL_DIR'"
check_exit 0 "tags-remove succeeds"

step "Show to verify 'urgent' is gone but 'work' remains." \
    "alph show '$NODE_TASK_META' --pool '$POOL_DIR'"
check_exit 0 "show after tag remove"
check_not_contains "'urgent' tag removed" "urgent"
check_contains "'work' tag still present" "work"

pause_between_sections

# ═══════════════════════════════════════════════════════════════════════════
section "Update Node — Edge Cases"
# ═══════════════════════════════════════════════════════════════════════════

explain "Update handles edge cases: no-op detection, not-found, invalid values."

step "No-op: update with current status value (should say 'unchanged')." \
    "alph update '$NODE_TASK' --status archived --pool '$POOL_DIR'"
check_exit 0 "no-op succeeds"
check_contains "no-op detected" "unchanged"

step "Not found: update a nonexistent node ID." \
    "alph update nonexistent1 --status archived --pool '$POOL_DIR' || true"
check_contains "not-found error" "not found"

step "Invalid status: update with a bogus status value." \
    "alph update '$NODE_TASK' --status bogus --pool '$POOL_DIR' || true"
check_contains "validation catches bad status" "status"

pause_between_sections

# ═══════════════════════════════════════════════════════════════════════════
section "End-to-End Lifecycle"
# ═══════════════════════════════════════════════════════════════════════════

explain "Full lifecycle: create a task, list it, update it, show it, validate."
explain "This mirrors the smoke test from the implementation plan."

step "Create a new task." \
    "alph add -c 'Deploy v2 to staging' --ct task --tags deploy --meta environment=staging --pool '$POOL_DIR' --creator tester@example.com"
check_exit 0 "lifecycle: create"
NODE_LIFECYCLE=$(extract_node_id)

step "List — should show the new task." \
    "alph list --pool '$POOL_DIR'"
check_exit 0 "lifecycle: list"
check_contains "new task in list" "Deploy v2"

step "Update — mark as archived with a 'done' tag." \
    "alph update '$NODE_LIFECYCLE' --status archived --tags-add done --pool '$POOL_DIR'"
check_exit 0 "lifecycle: update"

step "Show — verify archived status, both tags, and meta." \
    "alph show '$NODE_LIFECYCLE' --pool '$POOL_DIR'"
check_exit 0 "lifecycle: show"
check_contains "has deploy tag" "deploy"
check_contains "has done tag" "done"
check_contains "has environment meta" "environment"

step "List with -s all — should show everything." \
    "alph list --pool '$POOL_DIR' -s all"
check_exit 0 "lifecycle: list all"

step "Validate — entire pool should be clean." \
    "alph validate --pool '$POOL_DIR'"
check_exit 0 "lifecycle: validate"
check_contains "pool valid" "valid"

step "JSON output — verify structured output includes content_type." \
    "alph list --pool '$POOL_DIR' -s all -o json"
check_exit 0 "lifecycle: json output"
check_contains "content_type in json" "content_type"
check_contains "task type in json" "task"

# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}${BLUE}  Summary${RESET}"
echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

if [[ "$FAILURES" -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}  All checks passed.${RESET}"
else
    echo -e "${RED}${BOLD}  $FAILURES check(s) failed.${RESET}"
fi
echo ""
echo -e "  Temp directory: ${DIM}$TEST_DIR${RESET} (will be cleaned up on exit)"
echo ""
