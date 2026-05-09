#!/usr/bin/env bash
# =============================================================================
# sync_to_github.sh  —  Sync toolkit files to GitHub (respects .gitignore)
#
# Usage:
#   ./sync_to_github.sh -m "fix: ..."       # non-interactive: pass message
#   ./sync_to_github.sh                     # interactive: prompts for message
#   ./sync_to_github.sh --dry-run           # preview what would be committed
#   ./sync_to_github.sh -m "chore: ..." --dry-run
#
# When to use this script:
#   • Quick sync of small, focused changes (typos, doc tweaks, single-purpose
#     edits across a few files).
#
# When NOT to use it:
#   • Multiple unrelated changes — split them with `git add -p` + `git commit`
#     so the history stays useful (clean `git log` / `git blame`).
#   • Anything you'd describe with "and" in the message — that's two commits.
#
# The 2025_* project data folders are listed in .gitignore and will never be
# staged or pushed by this script.
# =============================================================================

set -euo pipefail

# ── helpers ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[sync]${RESET} $*"; }
success() { echo -e "${GREEN}[sync]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[sync]${RESET} $*"; }
error()   { echo -e "${RED}[sync] ERROR:${RESET} $*" >&2; }

# ── argument parsing ──────────────────────────────────────────────────────────
COMMIT_MSG=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--message)
            COMMIT_MSG="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            sed -n '/^# =====/,/^# =====/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            error "Unknown option: $1  (use -h for help)"
            exit 1
            ;;
    esac
done

# ── repo root check ───────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! git -C "$REPO_ROOT" rev-parse --git-dir &>/dev/null; then
    error "Not inside a git repository: $REPO_ROOT"
    exit 1
fi
cd "$REPO_ROOT"

# ── remote check ─────────────────────────────────────────────────────────────
REMOTE_URL="$(git remote get-url origin 2>/dev/null || true)"
if [[ -z "$REMOTE_URL" ]]; then
    error "No 'origin' remote configured. Add one with: git remote add origin <url>"
    exit 1
fi
info "Remote: ${BOLD}$REMOTE_URL${RESET}"

# ── stage everything not in .gitignore ───────────────────────────────────────
info "Staging changes (project data folders are excluded via .gitignore)…"
git add -A

# ── show what is staged ───────────────────────────────────────────────────────
STAGED="$(git diff --cached --stat)"
if [[ -z "$STAGED" ]]; then
    success "Nothing to commit — working tree is already in sync with remote."
    exit 0
fi

echo ""
echo -e "${BOLD}── Staged changes ──────────────────────────────────────${RESET}"
git diff --cached --stat
echo -e "${BOLD}────────────────────────────────────────────────────────${RESET}"
echo ""

# ── dry-run exit ──────────────────────────────────────────────────────────────
if $DRY_RUN; then
    warn "Dry-run mode: nothing was committed or pushed."
    exit 0
fi

# ── commit message ────────────────────────────────────────────────────────────
# No auto-suggested generic message: forces you to describe the change so
# `git log` stays a useful artifact instead of a wall of "sync toolkit files".
if [[ -z "$COMMIT_MSG" ]]; then
    echo -e "${BOLD}Commit message${RESET} (Conventional Commits prefix recommended:"
    echo -e "  ${YELLOW}feat / fix / docs / chore / refactor / test / ci / style${RESET})"
    echo -e "  e.g.  ${YELLOW}fix(echem): correct lineshape baseline window${RESET}"
    printf "> "
    read -r COMMIT_MSG
fi

if [[ -z "$COMMIT_MSG" ]]; then
    error "Commit message is required. Aborting."
    git reset HEAD > /dev/null  # unstage
    exit 1
fi

# Soft warning for vacuous messages
if [[ "$COMMIT_MSG" =~ ^(update|fix|wip|change|misc|sync|tweak|stuff|edit)$ ]]; then
    warn "Message '$COMMIT_MSG' is too vague. Consider a Conventional-Commits style line."
    printf "Continue anyway? [y/N] > "
    read -r ACK
    if [[ ! "$ACK" =~ ^[Yy]$ ]]; then
        git reset HEAD > /dev/null
        error "Aborted; nothing committed."
        exit 1
    fi
fi

# ── commit ────────────────────────────────────────────────────────────────────
info "Committing: \"$COMMIT_MSG\""
git commit -m "$COMMIT_MSG"

# ── push ──────────────────────────────────────────────────────────────────────
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
info "Pushing branch '${BOLD}$BRANCH${RESET}' to origin…"
git push origin "$BRANCH"

echo ""
success "Done! Toolkit synced to ${BOLD}$REMOTE_URL${RESET} (branch: $BRANCH)"
