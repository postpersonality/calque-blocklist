#!/usr/bin/env python3
"""Merge new banned calques (and/or accepted-jargon words) into calque-blocklist.json.
The ONLY sanctioned way to change the blocklist.

Banned payload shape ({lemma: {replace, kind, note}}):
    python3 merge_entry.py --json '{"тайтл": {"replace": ["заголовок"], "kind": "translit", "note": "title"}}'

Use `replace_set` instead of `replace` to OVERWRITE the existing replace list
(remove wrong alternatives, fix order). For new lemmas they behave the same.
    python3 merge_entry.py --json '{"коллизия": {"replace_set": ["конфликт", "пересечение"], "kind": "calque-sense"}}'

Add accepted-jargon words (never banned):
    python3 merge_entry.py --accepted "флоу,коммитить"

Flags:
    --dict PATH    blocklist json (default ~/.claude/calque-blocklist.json)
    --dry-run      show changes, do not write
"""
import argparse
import fcntl
import json
import os
import sys

HOME = os.path.expanduser("~")
DEFAULT_DICT = os.path.join(HOME, ".claude", "calque-blocklist.json")
KINDS = {"translit", "coined-verb", "untranslated", "pseudo-term", "calque-sense"}


def dedup_ci(items):
    seen, out = set(), []
    for v in items:
        if not isinstance(v, str) or not v.strip():
            continue
        lv = v.lower()
        if lv in seen:
            continue
        seen.add(lv)
        out.append(v)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dict", default=DEFAULT_DICT)
    ap.add_argument("--json", help="Inline {lemma: {replace, kind, note}}; else stdin (if no --accepted)")
    ap.add_argument("--accepted", default="", help="Comma-separated words to add to the accepted allowlist")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    payload = {}
    if args.json:
        payload = json.loads(args.json)
    elif not args.accepted and not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            payload = json.loads(raw)
    if not isinstance(payload, dict):
        print("Payload must be a JSON object {lemma: {...}}", file=sys.stderr)
        sys.exit(2)

    # Serialize concurrent writers: an exclusive flock around the whole
    # read-modify-write so two parallel merge_entry runs can't lose updates.
    lock_fd = open(args.dict + ".lock", "w")
    fcntl.flock(lock_fd, fcntl.LOCK_EX)

    with open(args.dict, encoding="utf-8") as f:
        cur = json.load(f)
    cur.setdefault("banned", {})
    cur.setdefault("accepted", [])

    added, merged, errors = {}, {}, []
    for lemma, e in payload.items():
        if not isinstance(e, dict) or "kind" not in e:
            errors.append(f"{lemma!r}: needs at least {{kind:..., replace:[...] or replace_set:[...]}}")
            continue
        has_replace = "replace" in e
        has_replace_set = "replace_set" in e
        if not has_replace and not has_replace_set:
            errors.append(f"{lemma!r}: needs replace or replace_set")
            continue
        if has_replace and has_replace_set:
            errors.append(f"{lemma!r}: pass replace OR replace_set, not both")
            continue
        if e["kind"] not in KINDS:
            errors.append(f"{lemma!r}: kind {e['kind']!r} not in {sorted(KINDS)}")
            continue
        repl = dedup_ci(e.get("replace_set") if has_replace_set else e.get("replace", []))
        if not repl:
            field = "replace_set" if has_replace_set else "replace"
            errors.append(f"{lemma!r}: {field} must be a non-empty list of strings")
            continue
        note = e.get("note", "")
        detect = dedup_ci(e.get("detect", []))  # optional surface stems for the hooks
        tier = e.get("tier")  # optional "substitute" | "hint"; default derived from kind by the hooks
        if tier is not None and tier not in ("substitute", "hint"):
            errors.append(f"{lemma!r}: tier must be 'substitute' or 'hint'")
            continue
        if lemma in cur["banned"]:
            ex = cur["banned"][lemma]
            if has_replace_set:
                prev = ex["replace"]
                removed = [r for r in prev if r.lower() not in {x.lower() for x in repl}]
                added_repl = [r for r in repl if r.lower() not in {x.lower() for x in prev}]
                if removed or added_repl or prev != repl:
                    ex["replace"] = repl
                    diff = {"now": repl}
                    if added_repl: diff["added"] = added_repl
                    if removed: diff["removed"] = removed
                    merged.setdefault(lemma, {})["replace_set"] = diff
            else:
                new = [r for r in repl if r.lower() not in {x.lower() for x in ex["replace"]}]
                if new:
                    ex["replace"] += new
                    merged.setdefault(lemma, {})["replace"] = new
            if e.get("kind") and e["kind"] != ex.get("kind"):
                ex["kind"] = e["kind"]; merged.setdefault(lemma, {})["kind"] = e["kind"]
            if note and note != ex.get("note"):
                ex["note"] = note; merged.setdefault(lemma, {})["note"] = note
            if detect:
                cur_det = ex.get("detect", [])
                add_det = [d for d in detect if d.lower() not in {x.lower() for x in cur_det}]
                if add_det:
                    ex["detect"] = cur_det + add_det
                    merged.setdefault(lemma, {})["detect"] = add_det
            if tier and tier != ex.get("tier"):
                ex["tier"] = tier; merged.setdefault(lemma, {})["tier"] = tier
        else:
            entry = {"replace": repl, "kind": e["kind"], "note": note}
            if detect:
                entry["detect"] = detect
            if tier:
                entry["tier"] = tier
            cur["banned"][lemma] = entry
            added[lemma] = entry

    if errors:
        print("ERRORS:\n  " + "\n  ".join(errors), file=sys.stderr)
        sys.exit(2)

    acc_added = []
    if args.accepted:
        for w in (x.strip() for x in args.accepted.split(",")):
            if w and w.lower() not in {a.lower() for a in cur["accepted"]}:
                cur["accepted"].append(w); acc_added.append(w)

    # Invariants: sort banned keys, dedup+sort accepted, no overlap.
    cur["banned"] = {k: cur["banned"][k] for k in sorted(cur["banned"])}
    cur["accepted"] = sorted(dedup_ci(cur["accepted"]), key=str.lower)
    overlap = {b.lower() for b in cur["banned"]} & {a.lower() for a in cur["accepted"]}
    if overlap:
        print(f"INVARIANT VIOLATION: words both banned and accepted: {sorted(overlap)}", file=sys.stderr)
        sys.exit(3)

    summary = {
        "dict": args.dict,
        "added": added,
        "merged_into_existing": merged,
        "accepted_added": acc_added,
        "totals": {"banned": len(cur["banned"]), "accepted": len(cur["accepted"])},
    }
    if args.dry_run:
        json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    tmp = args.dict + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, args.dict)
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
