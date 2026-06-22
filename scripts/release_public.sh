#!/usr/bin/env bash
# Automate the orphan-branch dance for republishing the public release.
#
#   bash scripts/release_public.sh [tag]
#
# What it does:
#   1. Verifies the current working tree is clean and on `main`.
#   2. Builds an orphan branch `_release_<timestamp>` from the current
#      HEAD's tracked files.
#   3. Squashes everything into one "Release <tag>" commit.
#   4. Force-pushes to `public main` (the public repo's main branch).
#   5. Returns to the original branch and deletes the local orphan.
#
# Pre-requisites:
#   - `public` remote configured (e.g. `git remote add public
#     https://github.com/<you>/kee.git`).
#   - `gh` CLI authed (only needed for `gh repo view` confirmation).
#   - `.gitignore` keeps personal content out (already done).

set -e

TAG="${1:-$(date -u +%Y-%m-%d)}"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
TS=$(date -u +%Y%m%d-%H%M%S)
ORPHAN="_release_${TS}"

if [ "$BRANCH" != "main" ]; then
  echo "FAIL: expected branch=main, got branch=$BRANCH"
  exit 1
fi

if ! git diff-index --quiet HEAD --; then
  echo "FAIL: working tree not clean. Commit or stash first."
  exit 1
fi

if ! git remote | grep -q '^public$'; then
  echo "FAIL: no `public` remote configured."
  echo "  git remote add public https://github.com/<you>/kee.git"
  exit 1
fi

ORIGIN=$(git rev-parse HEAD)
echo "Release tag:  $TAG"
echo "Source HEAD:  ${ORIGIN:0:12}"
echo "Orphan branch: $ORPHAN"
echo

# Build the orphan
git checkout --orphan "$ORPHAN"
echo "Files staged: $(git ls-files | wc -l)"

# Sanity check — bail if any obviously personal path leaked in
LEAKS=$(git ls-files | grep -E "vault/config/(user|identity|soul|goals)\.md$|vault/projects/|vault/_kee/|vault/\.obsidian/|AUDIT-|REPORT-|TEST-FLOW|\.local\.md$|google_(client|token)\.json|spotify_token\.json|\.env$" || true)
if [ -n "$LEAKS" ]; then
  echo
  echo "FAIL: personal paths leaked into orphan staging:"
  echo "$LEAKS"
  echo
  echo "Aborting. Add these to .gitignore (or git rm --cached) and retry."
  git checkout "$BRANCH"
  git branch -D "$ORPHAN"
  exit 2
fi

git -c user.email="noreply@anthropic.com" -c user.name="Kee" \
  commit -m "Release $TAG

Squashed snapshot from $ORIGIN.

See https://github.com/cocopsn/kee for setup, README.md for first-run,
docs/03-technical-roadmap-v2.md for the architectural spec."

echo
echo "Pushing to public main (force, single-commit history)..."
git push public "$ORPHAN:main" --force
echo
git checkout "$BRANCH"
git branch -D "$ORPHAN"

echo
echo "Done. Public main = single squashed commit at $TAG."
echo "Verify: gh repo view cocopsn/kee --web"
