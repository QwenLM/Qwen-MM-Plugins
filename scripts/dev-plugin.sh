#!/usr/bin/env bash
# Debug the full native plugin path (marketplace add + plugin install) against your LOCAL working
# tree instead of its stable tag: point the capability's marketplace entry back at this checkout
# and its MCP manifests at file://<repo> (with uvx --refresh), install, test, then revert.
# `marketplace add <this repo dir>` reads the working-tree manifests, so local wins.
#
#   scripts/dev-plugin.sh <cap> [<cap>...]          # flip selected manifests → local
#   scripts/dev-plugin.sh <cap> [<cap>...] --revert # restore their release refs
#   scripts/dev-plugin.sh all [--revert]            # every published capability
#
# --refresh makes uvx rebuild the local package on every launch (a bit slower, but always current).
# For rapid server-code iteration prefer `claude mcp add <name> -- python3 <server-dir>` instead
# (no build step at all — see docs/en/local_development.md).
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
mode=local
caps=()
for arg in "$@"; do
    case "$arg" in
        --revert) mode=restore ;;
        *) caps+=("$arg") ;;
    esac
done
[ ${#caps[@]} -gt 0 ] || { echo "usage: dev-plugin.sh <cap> [<cap>...] [--revert]"; exit 1; }

if [ "$mode" = restore ]; then
    python3 "$repo/scripts/rewrite_plugin_sources.py" --repo "$repo" --restore "${caps[@]}"
    echo "✓ restored release refs for: ${caps[*]}"
    exit 0
fi

python3 "$repo/scripts/rewrite_plugin_sources.py" --repo "$repo" --refresh "${caps[@]}"

echo "✓ flipped manifests → $repo  (file:// + uvx --refresh): ${caps[*]}"
echo "  claude plugin marketplace add $repo"
echo "  # install selected qwen-mm-plugins-<cap>@qwen-mm-plugins"
echo "  # revert when done:  scripts/dev-plugin.sh ${caps[*]} --revert"
