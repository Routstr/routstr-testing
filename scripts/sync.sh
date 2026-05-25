#!/usr/bin/env bash
set -euo pipefail

VENDOR_DIR="$(cd "$(dirname "$0")/.." && pwd)/vendor"
mkdir -p "$VENDOR_DIR"

REPOS=(
  "vendor/routstr-core|https://github.com/Routstr/routstr-core.git|main"
  "vendor/routstrd|https://github.com/Routstr/routstrd.git|main"
  "vendor/routstr-cli|https://github.com/Routstr/routstr-cli.git|main"
)

COMMITS_FILE="$VENDOR_DIR/COMMITS.txt"
> "$COMMITS_FILE"

for entry in "${REPOS[@]}"; do
  rel_path="${entry%%|*}"
  rest="${entry#*|}"
  url="${rest%%|*}"
  branch="${rest##*|}"
  abs_path="$(cd "$(dirname "$0")/.." && pwd)/$rel_path"

  if [ -d "$abs_path/.git" ]; then
    echo "Updating $rel_path ..."
    git -C "$abs_path" fetch --depth 1 origin "$branch"
    git -C "$abs_path" reset --hard "origin/$branch"
  else
    echo "Cloning $url into $rel_path ..."
    git clone --depth 1 --branch "$branch" "$url" "$abs_path"
  fi

  commit=$(git -C "$abs_path" rev-parse HEAD)
  echo "$rel_path $commit" >> "$COMMITS_FILE"
done

echo ""
echo "vendor/COMMITS.txt:"
cat "$COMMITS_FILE"
