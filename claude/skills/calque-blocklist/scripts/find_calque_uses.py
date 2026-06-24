#!/usr/bin/env python3
"""Surface how the ASSISTANT used a term across Claude Code session history.

For each query token (root/substring, case-insensitive), scans ASSISTANT text in
~/.claude/projects/**/*.jsonl, groups every occurrence by the exact word form it
appears in, and prints per group a count plus up to K most RECENT quotes with
surrounding context.

Purpose: give the agent enough context to translate the term correctly and to
tell apart distinct senses/forms that share a stem (e.g. the calque "слип" =
slip vs the unrelated legitimate "слипаться" = stick together). The tool does
NOT decide banned/accepted and prints NO verdict — the agent reads the groups
and contexts and judges sense, translation, and whether a `detect` stem would
collide with a legitimate word.

What counts as USAGE (everything else is NOT, and is skipped) — a CLOSED list of
structural exemptions, no keyword guessing:
  1-3. fenced code, <meta-discussion> zones, inline `code` — reused verbatim from
       calque-display.py (the single source of truth for "this is a marked mention,
       not usage").
  4.   any assistant message inside a /calque-blocklist launch turn (the skill's own
       work naturally names calques) — detected by the command envelope
       <command-name>/calque-blocklist, not a bare substring.
A calque written in bare prose is usage by definition (the discipline is to wrap it);
the scan reflects that and does not try to guess "discussion" from words like
"калька"/"дословно".

Scope is the AGENT's speech only — user messages are never scanned for usage:
whether a word is banned is the user's explicit command, not the user's own habits.

Recent-first because model/agent behaviour drifts over time; the latest usages
reflect current behaviour, not how the term was used long ago.

Usage:
    python3 find_calque_uses.py <token> [<token2> ...] [--context N] [--samples K]
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict

CLAUDE_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
GLOB = os.path.join(CLAUDE_HOME, "projects", "**", "*.jsonl")

# Reuse the display hook as the single source of truth for the structural model:
# prose_only (strips fenced code / <meta-discussion> / inline `code`), is_real_user_turn,
# turn_after_user, content_to_text, is_tool_result_user, and the regexes behind them.
# Nothing structural is re-defined here. Loaded by path because the filename is hyphenated.
_cd_path = os.path.join(CLAUDE_HOME, "hooks", "calque-display.py")
_spec = importlib.util.spec_from_file_location("calque_display", _cd_path)
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


def enclosing_word(txt, start, end):
    """Expand a match outward over word characters to the full word it sits in."""
    wordch = lambda ch: ch.isalnum() or ch in "-_"
    i = start
    while i > 0 and wordch(txt[i - 1]):
        i -= 1
    j = end
    while j < len(txt) and wordch(txt[j]):
        j += 1
    return txt[i:j], i, j


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tokens", nargs="+")
    ap.add_argument("--context", type=int, default=120, help="chars of context on each side of a quote")
    ap.add_argument("--samples", type=int, default=5, help="max quotes per word-form group (most recent first)")
    args = ap.parse_args()

    pats = [(t, re.compile(re.escape(t), re.I)) for t in args.tokens]
    files = sorted(glob.glob(GLOB, recursive=True))
    print(f"Scanning {len(files)} files…", file=sys.stderr)

    # occ[token][wordform] = list of (sortkey, date_label, snippet)
    occ = {t: defaultdict(list) for t, _ in pats}
    for fn in files:
        try:
            f = open(fn, encoding="utf-8")
        except OSError:
            continue
        with f:
            skill_turn = False
            for lineno, line in enumerate(f):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue

                if cd.is_real_user_turn(rec):
                    skill_turn = cd.turn_after_user(
                        skill_turn, cd.content_to_text(rec.get("message", {}).get("content")))
                    continue

                if rec.get("isSidechain") or rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                if msg.get("role") != "assistant":
                    continue
                if skill_turn:                       # exemption 4
                    continue

                ts = rec.get("timestamp") or ""
                prose = cd.prose_only(cd.content_to_text(msg.get("content")))  # exemptions 1-3
                for t, p in pats:
                    for m in p.finditer(prose):
                        word, wi, wj = enclosing_word(prose, m.start(), m.end())
                        lo = max(0, wi - args.context)
                        hi = min(len(prose), wj + args.context)
                        snippet = re.sub(r"\s+", " ", prose[lo:hi]).strip()
                        occ[t][word.lower()].append(((ts, fn, lineno), ts[:10], snippet))

    for t, _ in pats:
        groups = occ[t]
        total = sum(len(v) for v in groups.values())
        print(f"\n=== {t!r}: {total} occurrence(s) in assistant text across {len(groups)} word-form(s)")
        if not groups:
            print("  (not found)")
            continue
        for word in sorted(groups, key=lambda w: len(groups[w]), reverse=True):
            items = sorted(groups[word], key=lambda x: x[0], reverse=True)
            shown = min(args.samples, len(items))
            print(f"\n  [{word}]  ×{len(items)}  ({shown} most recent)")
            for _, date, snip in items[:args.samples]:
                d = f"{date} " if date else ""
                print(f"    - {d}…{snip}…")


if __name__ == "__main__":
    main()
