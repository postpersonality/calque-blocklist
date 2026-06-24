#!/usr/bin/env bash
# Copy the calque-blocklist payload into your Claude config dir (~/.claude by default).
# Does NOT touch settings.json or CLAUDE.md — those steps are manual (see README.md),
# because merging hooks into an existing settings.json needs your judgement.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

echo "Installing into: $DEST"
mkdir -p "$DEST"

# Never clobber an existing blocklist — it is the user's curated data.
if [ -e "$DEST/calque-blocklist.json" ]; then
  echo "  keep   calque-blocklist.json (already present, not overwritten)"
  cp -n "$HERE/claude/calque-blocklist.json" "$DEST/calque-blocklist.json.seed"
  echo "         seed copied as calque-blocklist.json.seed for reference"
else
  cp "$HERE/claude/calque-blocklist.json" "$DEST/"
  echo "  copy   calque-blocklist.json (seed list)"
fi

mkdir -p "$DEST/hooks" "$DEST/skills"
cp "$HERE/claude/hooks/calque-display.py" "$HERE/claude/hooks/calque-hint-inject.py" "$DEST/hooks/"
cp -r "$HERE/claude/skills/calque-blocklist" "$DEST/skills/"
chmod +x "$DEST/hooks/calque-display.py" "$DEST/hooks/calque-hint-inject.py" \
         "$DEST/skills/calque-blocklist/scripts/"*.py "$DEST/skills/calque-blocklist/scripts/validate.sh"
echo "  copy   hooks/ + skills/calque-blocklist/"

# Regenerate the stop-list from YOUR own history if there is any; otherwise ship the seed.
if compgen -G "$DEST/projects/*/*.jsonl" > /dev/null 2>&1; then
  python3 "$DEST/skills/calque-blocklist/scripts/render_stoplist.py" >/dev/null || true
  echo "  gen    calque-stoplist.md (from your transcripts)"
else
  cp -n "$HERE/claude/calque-stoplist.md" "$DEST/" || true
  echo "  copy   calque-stoplist.md (seed; regenerate later with render_stoplist.py)"
fi

echo
echo "Done copying. Two manual steps remain (see README.md):"
echo "  1. Add the two hooks from settings.hooks.json to $DEST/settings.json"
echo "  2. Add  @~/.claude/calque-stoplist.md  to your $DEST/CLAUDE.md"
echo
bash "$DEST/skills/calque-blocklist/scripts/validate.sh"
