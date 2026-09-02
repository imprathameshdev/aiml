#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# LotVulture Automated Release Script
#
# Automatically tags the current git commit with the standard date.hash
# nomenclature (vYYYY.MM.DD-<shortsha>) and pushes the tag to GitHub to
# trigger the automated Build & Release workflow.
#
# Usage:
#   ./release.sh
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# Ensure we are inside a git repo
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Error: Not inside a git repository."
    exit 1
fi

# Ensure git working tree has no uncommitted changes in tracked files (staged or unstaged)
GIT_STATUS="$(git status --porcelain=v1 --untracked-files=no)"
if [ -n "$GIT_STATUS" ]; then
    echo "============================================================"
    echo "Error: Working tree has uncommitted (staged or unstaged) changes!"
    echo "============================================================"
    git status --short --untracked-files=no
    echo "============================================================"
    echo "Commit or stash these tracked changes before creating a release tag."
    exit 1
fi

# Get current branch
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" != "master" ] && [ "$CURRENT_BRANCH" != "main" ]; then
    echo "Warning: Current branch is '$CURRENT_BRANCH', not 'master'."
    read -rp "Do you want to release from '$CURRENT_BRANCH'? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

# Ensure local commits are pushed to origin
UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
if [ -z "$UPSTREAM" ]; then
    if git rev-parse "origin/$CURRENT_BRANCH" >/dev/null 2>&1; then
        UPSTREAM="origin/$CURRENT_BRANCH"
    fi
fi

if [ -n "$UPSTREAM" ]; then
    # Fetch remote state quietly
    git fetch origin "$CURRENT_BRANCH" --quiet 2>/dev/null || true

    UNPUSHED="$(git log "$UPSTREAM..HEAD" --oneline)"
    if [ -n "$UNPUSHED" ]; then
        echo "============================================================"
        echo "Error: Local branch has unpushed commits!"
        echo "============================================================"
        echo "$UNPUSHED"
        echo "============================================================"
        echo "Push your commits ('git push') before creating a release tag."
        exit 1
    fi

    BEHIND="$(git log "HEAD..$UPSTREAM" --oneline)"
    if [ -n "$BEHIND" ]; then
        echo "============================================================"
        echo "Error: Local branch is behind $UPSTREAM!"
        echo "============================================================"
        echo "$BEHIND"
        echo "============================================================"
        echo "Pull latest changes ('git pull') before creating a release tag."
        exit 1
    fi
fi

# Compute date and git commit hash
DATE_STR="$(date -u +'%Y.%m.%d')"
SHORT_SHA="$(git rev-parse --short HEAD)"
TAG_NAME="v${DATE_STR}-${SHORT_SHA}"

# Check if tag already exists locally or remotely
if git rev-parse "$TAG_NAME" >/dev/null 2>&1; then
    echo "Tag '$TAG_NAME' already exists for this commit."
    read -rp "Force update and re-push tag '$TAG_NAME'? [y/N] " force_confirm
    if [[ "$force_confirm" =~ ^[Yy]$ ]]; then
        git tag -fa "$TAG_NAME" -m "Release $TAG_NAME"
        git push origin "$TAG_NAME" --force
        echo "Tag $TAG_NAME pushed successfully."
        exit 0
    else
        echo "Aborted."
        exit 1
    fi
fi

echo "============================================================"
echo "  Releasing: $TAG_NAME"
echo "  Commit:    $SHORT_SHA"
echo "  Date:      $DATE_STR"
echo "============================================================"

# Create annotated tag
git tag -a "$TAG_NAME" -m "Release $TAG_NAME"

# Push tag to remote
echo "Pushing tag to origin..."
git push origin "$TAG_NAME"

echo ""
echo "Done! Release tag '$TAG_NAME' pushed to GitHub."
echo "GitHub Actions is now building Docker images and Windows installers."
