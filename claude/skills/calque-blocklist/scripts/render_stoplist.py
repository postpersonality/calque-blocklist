#!/usr/bin/env python3
"""Render the short calque stop-list imported into ~/.claude/CLAUDE.md.

Reads the canonical blocklist, ranks detect-bearing calques by how often the
agent actually committed them in its OWN chat prose (assistant messages across
main session transcripts; fenced code / inline `code` / <meta-discussion> stripped,
/calque-blocklist launch turns skipped — the same structural model as the two
scanners), keeps the most frequent, and writes ~/.claude/calque-stoplist.md — the tiny
quick-reference list that ~/.claude/CLAUDE.md imports via `@`.

Derived artifact: NEVER hand-edit calque-stoplist.md. Re-run this script.
The principles (calque kinds, the full list, replacement rules) live in
CLAUDE.md §«Russian Communication Quality» — this file
is only the frequency-ranked top offenders, so it stays brief on purpose.

Usage:
  python3 render_stoplist.py                 # write, default thresholds
  python3 render_stoplist.py --min-count 30  # stricter cut
  python3 render_stoplist.py --max 8         # smaller cap
  python3 render_stoplist.py --dry-run       # print, do not write
"""
import argparse
import glob
import importlib.util
import json
import os
import re
from collections import Counter
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
BLOCKLIST = CLAUDE_HOME / "calque-blocklist.json"
OUT = CLAUDE_HOME / "calque-stoplist.md"
PROJECTS_GLOB = str(CLAUDE_HOME / "projects" / "*" / "*.jsonl")


# Single source of truth for the structural model: load the display hook (by path —
# hyphenated filename) and reuse its prose_only (strips fenced code / <meta-discussion>
# / inline `code`), is_real_user_turn, turn_after_user, content_to_text. The former
# keyword META_SKIP — which dropped a WHOLE message on any of калька/цитат/дослов/… and
# so hid genuine usage that merely shared a message with one of those words — is gone:
# bare prose is usage, structure (not keywords) marks a mention, and a /calque-blocklist
# launch turn is exempted by turn, exactly like the other two scanners.
_cd_path = CLAUDE_HOME / "hooks" / "calque-display.py"
_spec = importlib.util.spec_from_file_location("calque_display", str(_cd_path))
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


def count_freq(banned):
    """Count detect-stem hits per lemma across the agent's own chat prose.

    Structural exemptions (fenced code / <meta-discussion> / inline `code`) come from
    cd.prose_only; the whole-message exemption is a /calque-blocklist launch turn,
    tracked per file via cd.turn_after_user — the same model the display hook and
    find_calque_uses use. No keyword skip: a calque in bare prose counts even if the
    message also says «калька» or «цитата»."""
    pats = {
        lemma: [re.compile(r"\b" + re.escape(s) + r"[\wА-Яа-яЁё-]*", re.IGNORECASE) for s in e["detect"]]
        for lemma, e in banned.items()
        if e.get("detect")
    }
    counts = Counter()
    for fn in glob.glob(PROJECTS_GLOB):
        try:
            stream = open(fn, encoding="utf-8")
        except OSError:
            continue
        with stream:
            skill_turn = False
            for line in stream:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if cd.is_real_user_turn(rec):
                    skill_turn = cd.turn_after_user(
                        skill_turn, cd.content_to_text(rec.get("message", {}).get("content")))
                    continue
                if rec.get("type") != "assistant" or rec.get("isSidechain") or skill_turn:
                    continue
                prose = cd.prose_only(cd.content_to_text(rec.get("message", {}).get("content")))
                for lemma, ps in pats.items():
                    n = sum(len(p.findall(prose)) for p in ps)
                    if n:
                        counts[lemma] += n
    return counts


def qualifier(kind):
    if kind == "calque-sense":
        return " (в кальковом значении)"
    if kind == "untranslated":
        return " (латиницей в прозе)"
    return ""


def render(banned, ranked):
    lines = [
        "# Стоп-лист калек — самые частые",
        "",
        "Не употреблять в русской прозе; замена по контексту, для sense-калек запрет только в кальковом значении. "
        "Виды калек, правила и полный список — раздел «Russian Communication Quality» в ~/.claude/CLAUDE.md. "
        "Файл порождается скриптом, руками не править.",
        "",
    ]
    for lemma, _ in ranked:
        e = banned[lemma]
        repl = ", ".join(e["replace"][:3])
        lines.append(f"- «{lemma}»{qualifier(e['kind'])} → {repl}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=20, help="min occurrences to include (default 20)")
    ap.add_argument("--max", type=int, default=15, help="hard cap on list length (default 15)")
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    args = ap.parse_args()

    banned = json.loads(BLOCKLIST.read_text(encoding="utf-8"))["banned"]
    counts = count_freq(banned)
    ranked = [(l, n) for l, n in counts.most_common() if n >= args.min_count][: args.max]
    text = render(banned, ranked)

    if args.dry_run:
        print(text)
    else:
        OUT.write_text(text, encoding="utf-8")
    print(f"{'(dry-run) ' if args.dry_run else ''}{OUT} — {len(ranked)} calques (min_count={args.min_count}, max={args.max}):")
    for lemma, n in ranked:
        print(f"  {n:>4}  {lemma}")


if __name__ == "__main__":
    main()
