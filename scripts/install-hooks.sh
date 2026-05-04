#!/usr/bin/env bash
set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SCRIPTS_HOOKS_DIR="$REPO_ROOT/scripts/hooks"

for hook in pre-commit pre-push; do
  ln -sf "$SCRIPTS_HOOKS_DIR/$hook" "$HOOKS_DIR/$hook"
  echo "Installed $hook"
done

echo "Git hooks installed."
