#!/usr/bin/env bash
# Validate calque-blocklist.json invariants.
set -euo pipefail
DICT="${1:-$HOME/.claude/calque-blocklist.json}"

python3 - "$DICT" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
banned = d.get("banned", {}); accepted = d.get("accepted", [])
KINDS = {"translit","coined-verb","untranslated","pseudo-term","calque-sense"}
errs = []
for lemma, e in banned.items():
    if not isinstance(e.get("replace"), list) or not e["replace"]:
        errs.append(f"{lemma}: replace must be a non-empty list")
    if e.get("kind") not in KINDS:
        errs.append(f"{lemma}: bad kind {e.get('kind')!r}")
overlap = {b.lower() for b in banned} & {a.lower() for a in accepted}
if overlap:
    errs.append(f"both banned and accepted: {sorted(overlap)}")
if list(banned) != sorted(banned):
    errs.append("banned keys not sorted (run merge_entry.py to fix)")
if errs:
    print("FAIL:\n  " + "\n  ".join(errs)); sys.exit(1)
print(f"OK: {len(banned)} banned, {len(accepted)} accepted, JSON valid, invariants hold")
PY
