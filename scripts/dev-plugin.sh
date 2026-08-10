#!/usr/bin/env bash
# Debug the full native plugin path (marketplace add + plugin install) against your LOCAL working
# tree instead of git@main: point a capability's plugin manifests at file://<repo> (with uvx
# --refresh so every launch rebuilds from your source), install, test, then revert.
# `marketplace add <this repo dir>` reads the working-tree manifests, so local wins.
#
#   scripts/dev-plugin.sh <cap>            # flip <cap>'s manifests → local (file:// + --refresh)
#   scripts/dev-plugin.sh <cap> --revert   # restore the manifests (git checkout)
#     <cap> = core | video-memory | video-edit | example
#
# --refresh makes uvx rebuild the local package on every launch (a bit slower, but always current).
# For rapid server-code iteration prefer `claude mcp add <name> -- python3 <server-dir>` instead
# (no build step at all — see docs/en/local_development.md).
#
# The actual manifest-rewriting lives in scripts/_flip_mcp.py — install.sh's `localize`
# subcommand reuses the same script so the two paths can never drift.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
cap="${1:?usage: dev-plugin.sh <cap> [--revert]   (cap = core|video-memory|video-edit|example)}"

files=()
for f in "$repo/src/capabilities/$cap/.claude-plugin/plugin.json" "$repo/src/capabilities/$cap/.mcp.json"; do
    [ -f "$f" ] && files+=("$f")
done
[ ${#files[@]} -gt 0 ] || { echo "no plugin manifests found for capability '$cap'"; exit 1; }

if [ "${2:-}" = "--revert" ]; then
    git -C "$repo" checkout -- "${files[@]}"
    echo "✓ reverted $cap manifests"
    exit 0
fi

REPO="$repo" FLIP_ADD_REFRESH=1 "$repo/scripts/_flip_mcp.py" "${files[@]}"

echo "✓ flipped $cap manifests → $repo  (file:// + uvx --refresh)"
echo "  claude plugin marketplace add $repo"
echo "  claude plugin install qwen-mm-plugins-$cap@qwen-mm-plugins"
echo "  # revert when done:  scripts/dev-plugin.sh $cap --revert"
