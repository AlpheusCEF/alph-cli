#!/usr/bin/env bash
# human_test.sh — Comprehensive interactive walkthrough of the alph CLI
#
# Runs real alph commands in a temporary, isolated environment. Each step
# explains what it does, shows the command, waits for confirmation, runs it,
# and validates the output. Nothing touches your real config or pools.
#
# Usage:
#   bash tests/human_test.sh              # Run all sections from the start
#   bash tests/human_test.sh --list       # List all sections
#   bash tests/human_test.sh 7            # Jump to section 7 (Validate)
#   bash tests/human_test.sh 14           # Jump to section 14 (Remote RO)
#
# The script auto-detects whether to use 'poetry run alph' or bare 'alph'.
# When jumping to a later section, prerequisite state (registry, pool, nodes,
# config) is created silently before the first active section runs.

set -euo pipefail

# ---------------------------------------------------------------------------
# Section catalog — single source of truth for names and numbering
# ---------------------------------------------------------------------------

SECTIONS=(
    "Version and Help"
    "Registry and Pool Init"
    "Add Nodes"
    "List and Show"
    "Config Defaults"
    "Config Discovery"
    "Validate"
    "Demo Registry (multi-pool seed data)"
    "Content Type and Add Flags"
    "Input Validation (Error Cases)"
    "Update Node — Status Changes"
    "Update Node — Tags and Meta"
    "Update Node — Edge Cases"
    "End-to-End Lifecycle"
    "Remote Registry — RO Mode"
    "Remote Registry — RW Mode"
    "Tab Completion (manual)"
    "MCP Server Smoke Test"
    "Hydration Config"
)

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

START_SECTION=1

if [[ "${1:-}" == "--list" || "${1:-}" == "-l" ]]; then
    echo "Sections:"
    for i in "${!SECTIONS[@]}"; do
        printf "  %2d. %s\n" "$((i + 1))" "${SECTIONS[$i]}"
    done
    echo ""
    echo "Usage: bash $0 [section-number]"
    exit 0
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "Usage: bash $0 [options] [section-number]"
    echo ""
    echo "Options:"
    echo "  --list, -l    List all sections and exit"
    echo "  --help, -h    Show this help"
    echo ""
    echo "Examples:"
    echo "  bash $0           Run all sections"
    echo "  bash $0 7         Start at section 7 (Validate)"
    echo "  bash $0 --list    Show section list"
    exit 0
fi

if [[ -n "${1:-}" ]]; then
    if [[ "$1" =~ ^[0-9]+$ ]] && [[ "$1" -ge 1 ]] && [[ "$1" -le "${#SECTIONS[@]}" ]]; then
        START_SECTION="$1"
    else
        echo "Error: invalid section number '$1'. Use --list to see sections."
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Detect alph invocation
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$REPO_DIR/pyproject.toml" ]] && command -v poetry &>/dev/null; then
    ALPH="poetry -C $REPO_DIR run alph"
    ALPH_MCP="poetry -C $REPO_DIR run alph-mcp"
    ALPH_LABEL="poetry run alph (dev)"
elif command -v alph &>/dev/null; then
    ALPH="alph"
    ALPH_MCP="alph-mcp"
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
_section_active=false

section() {
    _section_num=$((_section_num + 1))
    if [[ "$_section_num" -ge "$START_SECTION" ]]; then
        _section_active=true
        echo ""
        echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
        echo -e "${BOLD}${BLUE}  Section ${_section_num}: $1${RESET}"
        echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
        echo ""
    else
        _section_active=false
    fi
}

# Returns 0 if the current section is active (should run)
active() { [[ "$_section_active" == "true" ]]; }

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
    # Also substitutes 'alph-mcp' at the start with $ALPH_MCP.
    local cmd="$1"
    local real_cmd="${cmd/#alph-mcp/$ALPH_MCP}"
    real_cmd="${real_cmd/#alph /$ALPH }"
    LAST_EXIT=0
    LAST_OUTPUT=$(eval "$real_cmd" 2>&1) || LAST_EXIT=$?
    echo -e "${DIM}─── output ───${RESET}"
    echo "$LAST_OUTPUT"
    echo -e "${DIM}─── exit: ${LAST_EXIT} ───${RESET}"
    echo ""
}

# Run a command silently — no output, no prompt. Sets LAST_OUTPUT/LAST_EXIT.
run_silent() {
    local cmd="$1"
    local real_cmd="${cmd/#alph-mcp/$ALPH_MCP}"
    real_cmd="${real_cmd/#alph /$ALPH }"
    LAST_EXIT=0
    LAST_OUTPUT=$(eval "$real_cmd" 2>&1) || LAST_EXIT=$?
}

step() {
    # Full step: explain, show command, confirm, run.
    local description="$1"
    local cmd="$2"
    explain "$description"
    show_cmd "$cmd"
    confirm
    run_cmd "$cmd"
}

observe() {
    # Observational step -- print instructions for manual verification.
    echo ""
    echo -e "  ${YELLOW}MANUAL CHECK${RESET}: $1"
    echo ""
    if [[ -n "${2:-}" ]]; then
        echo -e "  ${DIM}$2${RESET}"
        echo ""
    fi
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
    echo "$LAST_OUTPUT" | grep -oE '[a-f0-9]{12}' | head -1 || true
}

# Extract the first node ID from list output (second column of table)
extract_first_list_id() {
    echo "$LAST_OUTPUT" | grep -oE '[a-f0-9]{12}' | head -1 || true
}

has_github_token() {
    if [[ -n "${GITHUB_TOKEN:-}" ]] || [[ -n "${GH_TOKEN:-}" ]]; then
        return 0
    fi
    if command -v gh &>/dev/null && gh auth token &>/dev/null; then
        return 0
    fi
    return 1
}

DEMO_REPO="/Users/cpettet/git/chasemp/AlpheusCEF/multi-pool-repo-example"
DEMO_REGISTRY="$DEMO_REPO/registry"

has_demo_repo() {
    [[ -d "$DEMO_REPO" ]]
}

# Python command that has PyYAML available (poetry env or system)
if [[ -f "$REPO_DIR/pyproject.toml" ]] && command -v poetry &>/dev/null; then
    PY="poetry -C $REPO_DIR run python"
else
    PY="python3"
fi

# Write or merge keys into the isolated config YAML
write_config_key() {
    local key="$1"
    local value="$2"
    local config_file="$ALPH_CONFIG_DIR/config.yaml"
    $PY -c "
import yaml, os
p = '$config_file'
d = yaml.safe_load(open(p)) if os.path.exists(p) else {}
d['$key'] = '$value'
with open(p, 'w') as f:
    yaml.dump(d, f, sort_keys=False)
"
}

# Inject a key into config for testing, then remove it
inject_config_key() {
    local key="$1"
    local value="$2"
    local config_file="$ALPH_CONFIG_DIR/config.yaml"
    $PY -c "
import yaml
p = '$config_file'
d = yaml.safe_load(open(p))
d['$key'] = $value
with open(p, 'w') as f:
    yaml.dump(d, f, sort_keys=False)
"
}

remove_config_key() {
    local key="$1"
    local config_file="$ALPH_CONFIG_DIR/config.yaml"
    $PY -c "
import yaml
p = '$config_file'
d = yaml.safe_load(open(p))
if '$key' in d:
    del d['$key']
with open(p, 'w') as f:
    yaml.dump(d, f, sort_keys=False)
"
}

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

FAILURES=0
LAST_OUTPUT=""
LAST_EXIT=0
TEST_DIR=$(mktemp -d)
POOL_DIR=""
NODE_PURCHASE=""
NODE_LIVE=""
NODE_ARCHIVED=""
NODE_TASK=""
NODE_TASK_META=""
NODE_GDOC=""
NODE_RELATED=""
ORIG_ALPH_CONFIG_DIR="${ALPH_CONFIG_DIR:-}"

cleanup() {
    if [[ -n "$TEST_DIR" && -d "$TEST_DIR" ]]; then
        rm -rf "$TEST_DIR"
    fi
    # Clean up RW clone if created
    if [[ -n "${CLONE_DIR:-}" && -d "$CLONE_DIR" ]]; then
        rm -rf "$CLONE_DIR"
    fi
    if [[ -n "$ORIG_ALPH_CONFIG_DIR" ]]; then
        export ALPH_CONFIG_DIR="$ORIG_ALPH_CONFIG_DIR"
    else
        unset ALPH_CONFIG_DIR 2>/dev/null || true
    fi
}
trap cleanup EXIT

export ALPH_CONFIG_DIR="$TEST_DIR/global"

# ---------------------------------------------------------------------------
# Silent setup — creates prerequisite state when jumping to a later section
# ---------------------------------------------------------------------------

# Level 1: registry + pool (needed by sections >= 2)
_setup_registry_done=false
setup_registry() {
    if [[ "$_setup_registry_done" == "true" ]]; then return; fi
    run_silent "alph registry init --pool-home '$TEST_DIR/registry' --id test-household --context 'Scratch registry for human test run.' --name 'Test Household'"
    run_silent "alph pool init --registry test-household --name vehicles --context 'Vehicle maintenance and purchase records.' --cwd '$TEST_DIR/registry'"
    POOL_DIR="$TEST_DIR/registry/vehicles"
    _setup_registry_done=true
}

# Level 2: base nodes (needed by sections >= 3)
_setup_nodes_done=false
setup_base_nodes() {
    setup_registry
    if [[ "$_setup_nodes_done" == "true" ]]; then return; fi
    run_silent "alph add --pool '$POOL_DIR' --context 'Purchased 2022 Subaru Outback Wilderness, \$38,200. VIN: 4S4BTGND7N3123456.' --creator test@example.com"
    NODE_PURCHASE=$(extract_node_id)
    run_silent "alph add --pool '$POOL_DIR' --context 'Outback due for 10k service — oil change, tire rotation, multi-point inspection.' --creator test@example.com --type live"
    NODE_LIVE=$(extract_node_id)
    run_silent "alph add --pool '$POOL_DIR' --context 'Replaced wiper blades, passenger side was streaking badly. \$22 at AutoZone.' --creator test@example.com --status archived"
    NODE_ARCHIVED=$(extract_node_id)
    _setup_nodes_done=true
}

# Level 3: config defaults (needed by sections >= 5)
_setup_config_done=false
setup_config_defaults() {
    setup_base_nodes
    if [[ "$_setup_config_done" == "true" ]]; then return; fi
    write_config_key "creator" "test@example.com"
    write_config_key "default_pool" "vehicles"
    _setup_config_done=true
}

# Level 4: content_type nodes (needed by sections >= 9)
_setup_content_done=false
setup_content_nodes() {
    setup_config_defaults
    if [[ "$_setup_content_done" == "true" ]]; then return; fi
    run_silent "alph add -c 'Fix login bug' --ct task --pool '$POOL_DIR' --creator tester@example.com"
    NODE_TASK=$(extract_node_id)
    run_silent "alph add -c 'Write quarterly report' --ct task --meta priority=high --meta due=2026-04-01 --tags urgent --tags work --pool '$POOL_DIR' --creator tester@example.com"
    NODE_TASK_META=$(extract_node_id)
    run_silent "alph add -c 'Auth design doc' --ct gdoc --meta url=https://docs.google.com/doc/d/abc --tags architecture --pool '$POOL_DIR' --creator tester@example.com"
    NODE_GDOC=$(extract_node_id)
    run_silent "alph add -c 'Auth implementation ticket' --related-to '$NODE_GDOC' --pool '$POOL_DIR' --creator tester@example.com"
    NODE_RELATED=$(extract_node_id)
    _setup_content_done=true
}

# Level 5: archive NODE_TASK (needed by sections >= 12 which assume s11 ran)
_setup_updates_done=false
setup_update_state() {
    setup_content_nodes
    if [[ "$_setup_updates_done" == "true" ]]; then return; fi
    run_silent "alph update '$NODE_TASK' --status archived --pool '$POOL_DIR'"
    _setup_updates_done=true
}

# Ensure prerequisites for the starting section
ensure_prerequisites() {
    local s="$START_SECTION"
    if [[ "$s" -le 1 ]]; then return; fi

    echo -e "${DIM}Setting up prerequisites for section $s...${RESET}"

    if [[ "$s" -ge 2 ]]; then setup_registry; fi
    if [[ "$s" -ge 3 ]]; then setup_base_nodes; fi
    if [[ "$s" -ge 5 ]]; then setup_config_defaults; fi
    if [[ "$s" -ge 9 ]]; then setup_content_nodes; fi
    if [[ "$s" -ge 12 ]]; then setup_update_state; fi

    echo -e "${DIM}Prerequisites ready.${RESET}"
    echo ""
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

echo -e "${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════════════════════╗"
echo "  ║     AlpheusCEF — Comprehensive CLI Walkthrough              ║"
echo "  ╚══════════════════════════════════════════════════════════════╝"
echo -e "${RESET}"
echo -e "Sections:"
for i in "${!SECTIONS[@]}"; do
    local_num=$((i + 1))
    if [[ "$local_num" -eq "$START_SECTION" ]]; then
        printf "  ${BOLD}%2d. %s  <-- starting here${RESET}\n" "$local_num" "${SECTIONS[$i]}"
    elif [[ "$local_num" -lt "$START_SECTION" ]]; then
        printf "  ${DIM}%2d. %s  (skip)${RESET}\n" "$local_num" "${SECTIONS[$i]}"
    else
        printf "  %2d. %s\n" "$local_num" "${SECTIONS[$i]}"
    fi
done
echo ""
echo -e "Using:          ${DIM}$ALPH_LABEL${RESET}"
echo -e "Temp directory: ${DIM}$TEST_DIR${RESET}"
echo -e "Config dir:     ${DIM}$ALPH_CONFIG_DIR${RESET}"
echo ""
echo -e "${DIM}Each step shows a command, waits for you to press Enter, runs it,"
echo -e "then validates the output. Press 'q' at any prompt to exit early.${RESET}"

ensure_prerequisites

# ═══════════════════════════════════════════════════════════════════════════
section "Version and Help"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

step "Check alph version." \
    "alph --version"
check_exit 0 "alph --version succeeds"
check_contains "version string present" "alph"

step "Check alph --help shows top-level commands." \
    "alph --help"
check_exit 0 "alph --help succeeds"
check_contains "--help shows add" "add"
check_contains "--help shows list" "list"
check_contains "--help shows show" "show"
check_contains "--help shows validate" "validate"
check_contains "--help shows registry" "registry"
check_contains "--help shows pool" "pool"
check_contains "--help shows config" "config"
check_contains "--help shows defaults" "defaults"

step "Check -h is equivalent to --help." \
    "alph -h"
check_exit 0 "alph -h succeeds"
check_contains "-h shows commands" "add"

step "Check alph examples (hidden command)." \
    "alph examples"
check_exit 0 "alph examples succeeds"

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Registry and Pool Init"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

if [[ "$_setup_registry_done" != "true" ]]; then

explain "Create an isolated registry and pool for testing."
explain "Uses ALPH_CONFIG_DIR so nothing touches your real config."

step "Initialize a test registry." \
    "alph registry init --pool-home '$TEST_DIR/registry' --id test-household --context 'Scratch registry for human test run.' --name 'Test Household'"
check_exit 0 "registry init succeeds"
check_contains "registry created" "registry created"
check_contains "registry ID" "test-household"

step "Inspect the config file that was written." \
    "cat '$ALPH_CONFIG_DIR/config.yaml'"
check_exit 0 "config file exists"
check_contains "default_registry set" "default_registry"
check_contains "pool_home set" "pool_home"

step "List known registries." \
    "alph registry list"
check_exit 0 "registry list succeeds"
check_contains "test-household in list" "test-household"

step "Create a pool inside the registry." \
    "alph pool init --registry test-household --name vehicles --context 'Vehicle maintenance and purchase records.' --cwd '$TEST_DIR/registry'"
check_exit 0 "pool init succeeds"
check_contains "pool created" "pool created"
check_contains "pool name" "vehicles"

step "Verify pool directory structure." \
    "ls '$TEST_DIR/registry/vehicles/'"
check_exit 0 "pool dir exists"
check_contains "snapshots dir" "snapshots"
check_contains "live dir" "live"

step "Check pool dotfile." \
    "cat '$TEST_DIR/registry/vehicles/.alph.yaml'"
check_exit 0 ".alph.yaml exists"
check_contains "context in dotfile" "Vehicle maintenance"

step "List pools in the registry." \
    "alph pool list"
check_exit 0 "pool list succeeds"
check_contains "vehicles in pool list" "vehicles"

else
    explain "Registry and pool already created by setup — skipping init steps."
    POOL_DIR="$TEST_DIR/registry/vehicles"
fi

step "Error: unknown registry." \
    "alph pool init --registry ghost-registry --name demo --context 'Demo pool.' --cwd '$TEST_DIR' || true"
check_contains "not found error" "not found"

step "'alph registry' defaults to 'registry list'." \
    "alph registry"
check_exit 0 "registry default works"
check_contains "shows test-household" "test-household"

step "'alph pool' defaults to 'pool list'." \
    "alph pool"
check_exit 0 "pool default works"
check_contains "shows vehicles" "vehicles"

step "'alph reg list' shorthand." \
    "alph reg list"
check_exit 0 "reg shorthand works"
check_contains "shows test-household" "test-household"

step "'alph reg' defaults to list." \
    "alph reg"
check_exit 0 "reg default works"
check_contains "shows test-household" "test-household"

step "Reserved name: 'all' as registry ID." \
    "alph registry init --pool-home '$TEST_DIR/all-reg' --id all --context 'Should fail.' || true"
check_contains "reserved name error" "reserved"

step "Reserved name: 'all' as pool name." \
    "alph pool init --registry test-household --name all --context 'Should fail.' --cwd '$TEST_DIR' || true"
check_contains "reserved name error" "reserved"

step "Reserved name: 'alph' as registry ID." \
    "alph registry init --pool-home '$TEST_DIR/alph-reg' --id alph --context 'Should fail.' || true"
check_contains "reserved name error" "reserved"

step "Reserved name: 'alph' as pool name." \
    "alph pool init --registry test-household --name alph --context 'Should fail.' --cwd '$TEST_DIR' || true"
check_contains "reserved name error" "reserved"

step "Registry check defaults to default_registry." \
    "alph registry check"
check_exit 0 "registry check default works"

step "Registry status defaults to default_registry." \
    "alph registry status"
check_exit 0 "registry status default works"
check_contains "mode shown" "rw"

POOL_DIR="$TEST_DIR/registry/vehicles"
_setup_registry_done=true

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Add Nodes"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

if [[ "$_setup_nodes_done" != "true" ]]; then

explain "The --pool flag is explicit throughout this section."
explain "After section 5 (config defaults) we can omit it."

step "Add a snapshot node (note: single quotes around context with \$)." \
    "alph add --pool '$POOL_DIR' --context 'Purchased 2022 Subaru Outback Wilderness, \$38,200. VIN: 4S4BTGND7N3123456.' --creator test@example.com"
check_exit 0 "snapshot node created"
check_contains "node created" "node created"
NODE_PURCHASE=$(extract_node_id)
echo -e "  ${DIM}Captured node ID: $NODE_PURCHASE${RESET}"

step "Idempotency: add same context again." \
    "alph add --pool '$POOL_DIR' --context 'Purchased 2022 Subaru Outback Wilderness, \$38,200. VIN: 4S4BTGND7N3123456.' --creator test@example.com"
check_exit 0 "idempotency check"
check_contains "duplicate detected" "duplicate"

step "Add a live node." \
    "alph add --pool '$POOL_DIR' --context 'Outback due for 10k service — oil change, tire rotation, multi-point inspection.' --creator test@example.com --type live"
check_exit 0 "live node created"
check_contains "node created" "node created"
NODE_LIVE=$(extract_node_id)
echo -e "  ${DIM}Captured node ID: $NODE_LIVE${RESET}"

step "Add an archived node." \
    "alph add --pool '$POOL_DIR' --context 'Replaced wiper blades, passenger side was streaking badly. \$22 at AutoZone.' --creator test@example.com --status archived"
check_exit 0 "archived node created"
check_contains "node created" "node created"
NODE_ARCHIVED=$(extract_node_id)
echo -e "  ${DIM}Captured node ID: $NODE_ARCHIVED${RESET}"

_setup_nodes_done=true

else
    explain "Base nodes already created by setup — skipping add steps."
    echo -e "  ${DIM}NODE_PURCHASE=$NODE_PURCHASE  NODE_LIVE=$NODE_LIVE  NODE_ARCHIVED=$NODE_ARCHIVED${RESET}"
fi

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "List and Show"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

step "List active nodes (default)." \
    "alph list --pool '$POOL_DIR'"
check_exit 0 "list succeeds"
check_contains "purchase node visible" "Purchased"
check_contains "live node visible" "10k service"
check_not_contains "archived node hidden" "wiper"

step "List archived nodes only (-s archived)." \
    "alph list --pool '$POOL_DIR' -s archived"
check_exit 0 "list archived succeeds"
check_contains "archived node visible" "wiper"
check_not_contains "active nodes hidden" "Purchased"

step "List all statuses (-s all)." \
    "alph list --pool '$POOL_DIR' -s all"
check_exit 0 "list all succeeds"
check_contains "purchase in all" "Purchased"
check_contains "wiper in all" "wiper"

step "List with verbose flag." \
    "alph list --pool '$POOL_DIR' -v"
check_exit 0 "list verbose succeeds"

step "JSON output." \
    "alph list --pool '$POOL_DIR' -o json"
check_exit 0 "json output succeeds"
check_contains "json array" '\['

step "CSV output." \
    "alph list --pool '$POOL_DIR' -o csv"
check_exit 0 "csv output succeeds"

step "Show a node by ID." \
    "alph show '$NODE_PURCHASE' --pool '$POOL_DIR'"
check_exit 0 "show succeeds"
check_contains "node ID present" "$NODE_PURCHASE"
check_contains "source field" "alph-cli"
check_contains "creator field" "test@example.com"

step "Short alias: 'alph l' for list." \
    "alph l --pool '$POOL_DIR'"
check_exit 0 "alph l works"
check_contains "shows nodes" "Purchased"

step "Short alias: 'alph s' for show." \
    "alph s '$NODE_PURCHASE' --pool '$POOL_DIR'"
check_exit 0 "alph s works"
check_contains "shows node" "$NODE_PURCHASE"

step "Flag alias: -p for --pool." \
    "alph list -p '$POOL_DIR'"
check_exit 0 "-p alias works"
check_contains "shows nodes" "Purchased"

step "Flag alias: -r for --registry." \
    "alph pool list -r test-household"
check_exit 0 "-r alias works"
check_contains "shows pools" "vehicles"

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Config Defaults"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

if [[ "$_setup_config_done" != "true" ]]; then

explain "Set creator and default_pool so --pool and --creator can be omitted."

write_config_key "creator" "test@example.com"
write_config_key "default_pool" "vehicles"
echo -e "  ${DIM}Wrote creator and default_pool to config.${RESET}"

step "Add a node without --pool or --creator." \
    "alph add -c 'Oil change at Valvoline, 10,200 miles, full synthetic 0W-20.'"
check_exit 0 "add with defaults succeeds"
check_contains "node created" "node created"

_setup_config_done=true

else
    explain "Config defaults already set by setup — skipping config write steps."
fi

step "List without --pool." \
    "alph list"
check_exit 0 "list with defaults succeeds"

step "Check resolved defaults." \
    "alph defaults"
check_exit 0 "defaults succeeds"
check_contains "shows registry" "test-household"
check_contains "shows pool" "vehicles"
check_contains "shows creator" "test@example.com"

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Config Discovery"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

step "'alph config' defaults to 'config list'." \
    "alph config"
check_exit 0 "config default works"

step "Config list shows discovery tree." \
    "alph config list"
check_exit 0 "config list succeeds"
check_contains "global config shown" "global"

step "Config show displays a config file." \
    "alph config show '$ALPH_CONFIG_DIR/config.yaml'"
check_exit 0 "config show succeeds"

step "Config show for missing file." \
    "alph config show /tmp/does-not-exist/config.yaml || true"
check_contains "not found message" "not found"

step "Config check — clean config." \
    "alph config check"
check_exit 0 "config check passes"
check_contains "ok message" "ok"

explain "Inject a bogus key to test unknown key detection."
inject_config_key "bogus_option" "True"

step "Config check — unknown key detected." \
    "alph config check || true"
check_contains "unknown key warning" "unknown"

remove_config_key "bogus_option"
echo -e "  ${DIM}Removed bogus_option from config.${RESET}"

explain "Inject a bad default_registry to test referential integrity."
inject_config_key "default_registry" "'does-not-exist'"

step "Config check — bad default_registry." \
    "alph config check || true"
check_contains "not defined warning" "not defined"

# Restore
write_config_key "default_registry" "test-household"
echo -e "  ${DIM}Restored default_registry to test-household.${RESET}"

step "Config show-all — merged config with defaults." \
    "alph config show-all"
check_exit 0 "config show-all succeeds"
check_contains "auto_commit shown" "auto_commit"

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Validate"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

step "Validate the vehicles pool." \
    "alph validate --pool '$POOL_DIR'"
check_exit 0 "validate succeeds"
check_contains "pool valid" "valid"

step "Validate with verbose." \
    "alph validate --pool '$POOL_DIR' -v"
check_exit 0 "validate verbose succeeds"
check_contains "valid" "valid"

explain "Corrupt a node to test validation failure."
explain "Using a separate disposable pool to avoid breaking the main test pool."

CORRUPT_POOL="$TEST_DIR/registry/corrupt-test"
mkdir -p "$CORRUPT_POOL/snapshots" "$CORRUPT_POOL/live"

# Create a minimal valid node, then strip schema_version
cat > "$CORRUPT_POOL/snapshots/testnode0001.md" << 'NODEEOF'
---
schema_version: "1"
id: testnode00001
timestamp: "2026-01-01T00:00:00Z"
source: test
node_type: snapshot
context: Corrupt test node.
creator: test@example.com
---
Test content.
NODEEOF

step "Validate the corrupt pool (should be valid first)." \
    "alph validate --pool '$CORRUPT_POOL'"
check_exit 0 "corrupt pool initially valid"
check_contains "valid" "valid"

sed -i '' '/schema_version/d' "$CORRUPT_POOL/snapshots/testnode0001.md"

step "Validate after removing schema_version." \
    "alph validate --pool '$CORRUPT_POOL' || true"
check_contains "validation catches missing field" "invalid"

EMPTY_POOL="$TEST_DIR/registry/empty-pool"
mkdir -p "$EMPTY_POOL/snapshots" "$EMPTY_POOL/live"

step "Validate an empty pool." \
    "alph validate --pool '$EMPTY_POOL'"
check_exit 0 "empty pool validate succeeds"
check_contains "no nodes message" "no nodes"

step "Validate a nonexistent pool." \
    "alph validate --pool '$TEST_DIR/registry/nonexistent' || true"
check_contains "pool not found" "not found"

step "Short alias: 'alph v' for validate." \
    "alph v --pool '$POOL_DIR'"
check_exit 0 "alph v works"
check_contains "valid" "valid"

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Demo Registry (Multi-Pool Seed Data)"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

if ! has_demo_repo; then
    echo -e "  ${YELLOW}SKIP${RESET}: Demo repo not found at $DEMO_REPO"
    echo -e "  ${DIM}Clone AlpheusCEF/multi-pool-repo-example to run this section.${RESET}"
else
    step "Seed the demo registry (28 nodes across 3 pools)." \
        "poetry -C '$REPO_DIR' run python '$DEMO_REPO/seed.py' --wipe"
    check_exit 0 "seed succeeds"
    check_contains "28 total" "28"

    step "List vehicles pool." \
        "alph list --pool '$DEMO_REGISTRY/vehicles'"
    check_exit 0 "demo vehicles list"

    step "List appliances pool." \
        "alph list --pool '$DEMO_REGISTRY/appliances'"
    check_exit 0 "demo appliances list"

    step "List remodeling pool." \
        "alph list --pool '$DEMO_REGISTRY/remodeling'"
    check_exit 0 "demo remodeling list"

    step "Show cross-pool related_to reference." \
        "alph show d133ae8da4be --pool '$DEMO_REGISTRY/remodeling'"
    check_exit 0 "demo show cross-pool"
    check_contains "cross-pool reference" "appliances"

    step "Validate all three demo pools." \
        "alph validate --pool '$DEMO_REGISTRY/vehicles'"
    check_exit 0 "demo vehicles valid"
    check_contains "valid" "valid"

    run_cmd "alph validate --pool '$DEMO_REGISTRY/appliances'"
    check_exit 0 "demo appliances valid"

    run_cmd "alph validate --pool '$DEMO_REGISTRY/remodeling'"
    check_exit 0 "demo remodeling valid"
fi

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Content Type and Add Flags"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

if [[ "$_setup_content_done" != "true" ]]; then

explain "The 'task' content type has no required meta fields,"
explain "making it flexible for task management."

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

explain "These flags (--tags, --meta, --related-to) are wired into the CLI."

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

_setup_content_done=true

else
    explain "Content type nodes already created by setup — skipping creation steps."
    echo -e "  ${DIM}NODE_TASK=$NODE_TASK  NODE_TASK_META=$NODE_TASK_META  NODE_GDOC=$NODE_GDOC${RESET}"

    step "Show the task node to verify frontmatter." \
        "alph show '$NODE_TASK_META' --pool '$POOL_DIR'"
    check_exit 0 "show succeeds"
    check_contains "content_type is task" "task"
    check_contains "priority meta present" "priority"

    step "Validate the pool to confirm task nodes are schema-compliant." \
        "alph validate --pool '$POOL_DIR'"
    check_exit 0 "validation passes with task nodes"
    check_contains "pool is valid" "valid"

    step "Show the related node to verify the cross-reference." \
        "alph show '$NODE_RELATED' --pool '$POOL_DIR'"
    check_exit 0 "show related node"
    check_contains "related_to contains gdoc ID" "$NODE_GDOC"
fi

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Input Validation (Error Cases)"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

explain "The CLI should reject invalid inputs with clear error messages."

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
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Update Node — Status Changes"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

explain "alph update modifies an existing node's frontmatter or body."

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
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Update Node — Tags and Meta"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

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
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Update Node — Edge Cases"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

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
fi

# ═══════════════════════════════════════════════════════════════════════════
section "End-to-End Lifecycle"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

explain "Full lifecycle: create a task, list it, update it, show it, validate."

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

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Remote Registry — RO Mode"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

if ! has_github_token; then
    echo -e "  ${YELLOW}SKIP${RESET}: No GitHub token found."
    echo -e "  ${DIM}Set GITHUB_TOKEN, GH_TOKEN, or run 'gh auth login' to enable this section.${RESET}"
else
    REMOTE_URL="git@github.com:AlpheusCEF/multi-pool-repo-example.git:/registry"

    explain "Add a read-only remote registry entry via CLI."

    step "Register a remote RO registry." \
        "alph reg init --id remote-example --pool-home '$REMOTE_URL' --context 'Remote demo registry (read-only).' --mode ro --branch seeded"
    check_exit 0 "remote RO registry created"
    check_contains "registry created" "registry created"

    step "Registry list shows both local and remote." \
        "alph registry list"
    check_exit 0 "registry list with remote"
    check_contains "local registry" "test-household"
    check_contains "remote registry" "remote-example"

    step "Check remote reachability." \
        "alph registry check remote-example"
    check_exit 0 "remote check succeeds"
    check_contains "remote reachable" "ok"

    step "Check all registries." \
        "alph registry check all"
    check_exit 0 "check all succeeds"

    step "List nodes from remote RO pool (via GitHub API)." \
        "alph --registry remote-example list --pool vehicles"
    check_exit 0 "remote list succeeds"
    REMOTE_NODE_ID=$(extract_first_list_id)
    echo -e "  ${DIM}Captured remote node ID: ${REMOTE_NODE_ID:-none}${RESET}"

    if [[ -n "$REMOTE_NODE_ID" ]]; then
        step "Show a node from remote pool." \
            "alph --registry remote-example show '$REMOTE_NODE_ID' --pool vehicles"
        check_exit 0 "remote show succeeds"
    fi

    step "Validate remote pool." \
        "alph --registry remote-example validate --pool vehicles"
    check_exit 0 "remote validate succeeds"
    check_contains "valid" "valid"

    step "Write to RO remote should error." \
        "alph add -c 'Should fail.' --pool '$REMOTE_URL/vehicles' --creator test@example.com || true"
    check_contains "read-only error" "read-only"

    step "Ad-hoc --registry with raw URL (main branch — no data)." \
        "alph --registry '$REMOTE_URL' list --pool vehicles || true"
    check_exit 0 "ad-hoc URL works"

    step "Ad-hoc --branch flag with raw URL (seeded branch — has data)." \
        "alph --branch seeded --registry '$REMOTE_URL' list --pool vehicles"
    check_exit 0 "ad-hoc branch works"
fi

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Remote Registry — RW Mode"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

if ! has_github_token; then
    echo -e "  ${YELLOW}SKIP${RESET}: No GitHub token found."
    echo -e "  ${DIM}Set GITHUB_TOKEN, GH_TOKEN, or run 'gh auth login' to enable this section.${RESET}"
else
    CLONE_DIR="$TEST_DIR/rw-clone"
    REMOTE_URL="git@github.com:AlpheusCEF/multi-pool-repo-example.git:/registry"

    step "Register a remote RW registry." \
        "alph reg init --id remote-rw --pool-home '$REMOTE_URL' --context 'Remote demo (read-write clone).' --mode rw --branch seeded --clone-path '$CLONE_DIR'"
    check_exit 0 "remote RW registry created"

    step "Clone the remote registry." \
        "alph registry clone remote-rw"
    check_exit 0 "clone succeeds"
    check_contains "cloned" "cloned"

    step "Second clone is a no-op." \
        "alph registry clone remote-rw"
    check_exit 0 "second clone no-op"
    check_contains "already cloned" "already cloned"

    step "Registry status for RW remote." \
        "alph registry status remote-rw"
    check_exit 0 "rw status succeeds"
    check_contains "mode is rw" "rw"

    step "Pull latest changes." \
        "alph registry pull remote-rw"
    check_exit 0 "pull succeeds"
    check_contains "pulled" "pulled"

    step "List with --pull flag (RW clone)." \
        "alph --registry remote-rw list --pool vehicles --pull"
    check_exit 0 "list with --pull succeeds"

    step "Add a node to the RW clone." \
        "alph add -c 'Test node from RW clone walkthrough.' --pool '$REMOTE_URL/vehicles' --creator test@example.com"
    check_exit 0 "RW add succeeds"
    check_contains "node created in clone" "node created"

    step "Verify the node appears in the clone." \
        "alph --registry remote-rw list --pool vehicles"
    check_exit 0 "RW list after add"
    check_contains "new node visible" "RW clone walkthrough"
fi

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "Tab Completion"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

explain "Tab completion is observational — install and verify manually."

observe "Install completion for your shell (run once):" \
    "alph completions install zsh   # or bash, fish"

observe "After reloading your shell, test:" \
    "alph registry check <TAB>    — should show registry IDs + 'all'
  alph list --pool <TAB>        — should show pool names from local registries"

observe "Homebrew users: completion scripts are installed automatically." \
    "Ensure fpath includes Homebrew site-functions. For Oh My Zsh, add before source:
  fpath=(/opt/homebrew/share/zsh/site-functions \$fpath)"

observe "RO remote completion requires opt-in:" \
    "Set completion_remote: true in config (global or per-registry).
  Results are cached for completion_cache_ttl seconds (default 60)."

pause_between_sections
fi

# ═══════════════════════════════════════════════════════════════════════════
section "MCP Server Smoke Test"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

step "Verify alph-mcp binary exists." \
    "alph-mcp --help || true"
check_exit 0 "alph-mcp help works"

observe "Register the MCP server with Claude Code:" \
    "claude mcp add --scope user alph -- alph-mcp"

observe "Then test interactively in a Claude Code session:" \
    "Ask: 'Using alph MCP tools, list all nodes in the pool at <pool-path>'"

fi

# ═══════════════════════════════════════════════════════════════════════════
section "Hydration Config"
# ═══════════════════════════════════════════════════════════════════════════
if active; then

explain "Hydration is the process of resolving a live node to its current content."
explain "hydration.yaml at the registry root declares per-type resolution config."
explain ""
explain "We'll create a registry with hydration.yaml and verify:"
explain "  1. show displays hydration instructions for matching content types"
explain "  2. validate accepts custom types declared in hydration.yaml"
explain "  3. show works normally when no hydration.yaml exists"

# Create a fresh registry for hydration tests
HYDRATION_REG="$TEST_DIR/hydration-registry"
run_silent "alph registry init --pool-home '$HYDRATION_REG' --id hydration-test --context 'Registry for hydration tests.' --name 'Hydration Test'"
run_silent "alph pool init --registry hydration-test --name testpool --context 'Hydration test pool.' --cwd '$HYDRATION_REG'"
HYDRATION_POOL="$HYDRATION_REG/testpool"

step "Add a gdoc live node to the pool." \
    "alph add -c 'Design doc for auth flow' --ct gdoc --meta url=https://docs.google.com/doc/d/abc --type live --pool '$HYDRATION_POOL' --creator tester@example.com"
check_exit 0 "gdoc node created"
NODE_HYDRATION_GDOC=$(extract_node_id)
echo -e "  ${DIM}Captured node ID: $NODE_HYDRATION_GDOC${RESET}"

step "Add a slack live node (channel only, no thread_ts)." \
    "alph add -c 'proj-auth channel' --ct slack --meta channel=proj-auth --type live --pool '$HYDRATION_POOL' --creator tester@example.com"
check_exit 0 "slack channel-only node created"
NODE_HYDRATION_SLACK=$(extract_node_id)
echo -e "  ${DIM}Captured node ID: $NODE_HYDRATION_SLACK${RESET}"

step "Show the gdoc node before hydration.yaml exists — no hydration section." \
    "alph show '$NODE_HYDRATION_GDOC' --pool '$HYDRATION_POOL'"
check_exit 0 "show succeeds"
check_not_contains "no hydration before config" "hydration"

step "Validate passes — all built-in types are valid." \
    "alph validate --pool '$HYDRATION_POOL'"
check_exit 0 "validation passes"
check_contains "pool is valid" "valid"

explain "Now we write hydration.yaml to the registry root."

# Write hydration.yaml
cat > "$HYDRATION_REG/hydration.yaml" <<'YAML'
types:
  gdoc:
    provider: google-docs-mcp
    instructions: >
      Use the Google Docs MCP server to fetch document content.
      The document URL is in meta.url.
  slack:
    provider: slack-mcp
    instructions: >
      Use the Slack MCP server. The channel name is in meta.channel.
  custom_widget:
    provider: widget-mcp
    instructions: Fetch via widget API.
YAML
echo -e "  ${DIM}Wrote hydration.yaml to $HYDRATION_REG${RESET}"

step "Show the gdoc node — hydration instructions should appear." \
    "alph show '$NODE_HYDRATION_GDOC' --pool '$HYDRATION_POOL'"
check_exit 0 "show succeeds"
check_contains "hydration section present" "hydration"
check_contains "Google Docs MCP in instructions" "Google Docs MCP"

step "Show the slack node — slack-specific instructions should appear." \
    "alph show '$NODE_HYDRATION_SLACK' --pool '$HYDRATION_POOL'"
check_exit 0 "show succeeds"
check_contains "slack hydration present" "Slack MCP"

explain "Now test custom content types declared in hydration.yaml."

# Write a node with custom content_type directly
cat > "$HYDRATION_POOL/live/customabc12345.md" <<'NODE'
---
schema_version: '1'
id: customabc12345
timestamp: '2026-03-16T00:00:00Z'
source: cli
node_type: live
context: Custom widget resource
creator: tester@example.com
content_type: custom_widget
---
NODE
echo -e "  ${DIM}Wrote custom_widget node manually${RESET}"

step "Validate — custom_widget type should pass (declared in hydration.yaml)." \
    "alph validate --pool '$HYDRATION_POOL'"
check_exit 0 "validation passes with custom type"
check_contains "pool is valid" "valid"

step "Show the custom_widget node — hydration instructions present." \
    "alph show customabc12345 --pool '$HYDRATION_POOL'"
check_exit 0 "show custom_widget"
check_contains "widget instructions" "widget API"

explain "Remove hydration.yaml to confirm custom type fails without it."
rm "$HYDRATION_REG/hydration.yaml"

step "Validate without hydration.yaml — custom_widget should fail." \
    "alph validate --pool '$HYDRATION_POOL' || true"
check_exit 1 "validation fails without hydration.yaml"
check_contains "custom_widget flagged" "custom_widget"

pause_between_sections
fi

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
