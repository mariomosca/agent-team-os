#!/usr/bin/env bash
# install-git-hooks.sh (RS07-F4.1): the repo hooks (scripts/git-hooks) become the hooks of every worktree.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath scripts/git-hooks
echo "core.hooksPath = scripts/git-hooks"
