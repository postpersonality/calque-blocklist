#!/usr/bin/env python3
"""UserPromptSubmit hook: feed a pending «Анти-калька» remark into the model.

Companion to calque-display.py (MessageDisplay stasher). At turn end the stasher
records the calques it found in ~/.claude/calque_guard/<session>.pending.json, split
by severity ({"high": …, "low": …}). On the user's NEXT prompt this hook reads that
file and returns the remark as `additionalContext`, grouped by severity, so the
model takes it into account on the next step (no redo of the past turn). The
pending file is consumed (deleted) once injected. Any error -> exit 0.
"""
import json
import os
import re
import sys
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
STATE_DIR = CLAUDE_HOME / "calque_guard"


def fmt(items):
    out = []
    for lemma, r in sorted(items.items()):
        repl = ", ".join(r.get("replace", []))
        tail = f" ({r['note']})" if r.get("note") else ""
        out.append(f"  - «{lemma}» → {repl}{tail}")
    return "\n".join(out)


def main():
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    session_id = payload.get("session_id") or payload.get("sessionId") or ""
    if not session_id:
        return
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    pend = STATE_DIR / f"{sid}.pending.json"
    if not pend.exists():
        return
    # Consume by claiming first: rename to a private name, then read that. A concurrent
    # Stop write to <session>.pending.json can't interleave with the read this way.
    claimed = pend.with_suffix(f".{os.getpid()}.claim")
    try:
        os.replace(pend, claimed)
    except OSError:
        return
    try:
        data = json.loads(claimed.read_text(encoding="utf-8"))
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass

    high, low = data.get("high", {}), data.get("low", {})
    if not high and not low:
        return

    parts = ["Замечания «Анти-калька» к твоему предыдущему ответу "
             "(подсказка, прошлый ответ переделывать не нужно — учти в дальнейших формулировках):"]
    if high:
        parts.append("[критичность: высокая] Однозначные кальки (в показе пользователю уже "
                     "заменены на верное слово; используй замену и впредь):\n" + fmt(high))
    if low:
        parts.append("[критичность: низкая] Возможные кальки — проверь по смыслу, заменяй "
                     "только если применил как кальку:\n" + fmt(low))
    context = "\n\n".join(parts)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
