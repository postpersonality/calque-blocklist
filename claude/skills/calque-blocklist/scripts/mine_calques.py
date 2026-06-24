#!/usr/bin/env python3
"""Mine calque-correction windows from all Claude Code session jsonl files.

Scans ~/.claude/projects/*/*.jsonl, reads BOTH user-typed messages and assistant
text (calque flags live in user corrections AND in the agent's ru-check self-reports),
finds windows around calque markers, deduplicates near-identical windows (handoff docs
replicate text), and surfaces distinct windows + auto-extracted "X -> Y" pairs.

Output: a report to stdout / a file. Curate by hand afterwards.
"""
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

PROJECTS_GLOB = os.path.expanduser("~/.claude/projects/**/*.jsonl")

# Markers that flag a calque/anglicism correction or discussion.
MARKER_RE = re.compile(
    r"(кальк|англициз|ангициз|транслит|translit|калькир|англиц)",
    re.I,
)

# Skip non-user-typed envelopes when reading user role.
SKIP_PREFIXES = (
    "<local-command", "<command-name", "<command-message", "<command-stdout",
    "Caveat:", "<system-reminder", "[Request interrupted",
)


def is_skippable_user(text):
    t = text.lstrip()
    if t.startswith(SKIP_PREFIXES):
        return True
    if t.startswith("<tool_use_error>"):
        return True
    return False


def iter_texts(rec):
    """Yield (role, text) for user-typed and assistant text blocks."""
    if rec.get("isSidechain"):
        return
    typ = rec.get("type")
    msg = rec.get("message") or {}
    role = msg.get("role")
    content = msg.get("content")
    if typ == "user" and role == "user":
        if isinstance(content, str):
            if not is_skippable_user(content):
                yield ("user", content)
        elif isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    t = c.get("text", "")
                    if t and not is_skippable_user(t):
                        yield ("user", t)
    elif typ == "assistant" and role == "assistant":
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    t = c.get("text", "")
                    if t:
                        yield ("assistant", t)


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def main():
    files = sorted(glob.glob(PROJECTS_GLOB, recursive=True))
    print(f"Scanning {len(files)} files…", file=sys.stderr)

    # window text -> {count, roles, first_session}
    windows = {}
    win_count = Counter()
    win_role = defaultdict(set)
    arrow_pairs = Counter()  # "X -> Y" extracted near a marker

    # Arrow / replacement extraction inside a marker window.
    # (a) quoted both sides; (b) loose: token(s) -> token(s) without quotes.
    ARROW_RE = re.compile(r"[«\"']([^»\"'\n]{1,40})[»\"']\s*(?:->|→|⟶|=>)\s*[«\"']([^»\"'\n]{1,40})[»\"']")
    LOOSE_ARROW_RE = re.compile(r"([A-Za-zА-Яа-яЁё][\wА-Яа-яЁё '-]{1,30}?)\s*(?:->|→|⟶)\s*([A-Za-zА-Яа-яЁё][\wА-Яа-яЁё '-]{1,30})")

    for fn in files:
        sid = os.path.basename(fn)[:8]
        try:
            f = open(fn, encoding="utf-8")
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                for role, text in iter_texts(rec):
                    for m in MARKER_RE.finditer(text):
                        pos = m.start()
                        win = norm(text[max(0, pos - 160): pos + 160])
                        key = win.lower()
                        win_count[key] += 1
                        win_role[key].add(role)
                        if key not in windows:
                            windows[key] = (win, sid)
                        for am in ARROW_RE.finditer(win):
                            arrow_pairs[(norm(am.group(1)), norm(am.group(2)))] += 1
                        for am in LOOSE_ARROW_RE.finditer(win):
                            a, b = norm(am.group(1)), norm(am.group(2))
                            if a.lower() != b.lower():
                                arrow_pairs[(a, b)] += 1

    # Report
    out = []
    out.append(f"# Calque-correction mining report\n")
    out.append(f"scanned_files: {len(files)}")
    out.append(f"distinct_windows: {len(windows)}")
    out.append(f"distinct_arrow_pairs: {len(arrow_pairs)}\n")

    out.append("## Arrow pairs near calque markers (X -> Y), by frequency\n")
    for (a, b), n in arrow_pairs.most_common():
        out.append(f"[{n:>3}] «{a}» -> «{b}»")
    out.append("")

    out.append("## Distinct windows (deduped), by frequency\n")
    for key, n in win_count.most_common():
        win, sid = windows[key]
        roles = "/".join(sorted(win_role[key]))
        out.append(f"[{n:>3}|{roles}|{sid}] …{win}…")

    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
