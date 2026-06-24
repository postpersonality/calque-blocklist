#!/usr/bin/env python3
"""MessageDisplay hook for «Анти-калька» — the SINGLE detection point.

Fires per flush while an assistant message streams. `delta` is whole completed
lines (per the MessageDisplay contract): the model's ORIGINAL text, before
anything rewrites it. This is the only place that sees the true output. The Stop
hook used to re-read the transcript, but that snapshot is non-final and
unreliable (it caught streaming/display state, missing substitute calques one
turn and over-catching back-ticked ones the next), so detection lives here now.

On the original text, in non-exempt prose:
  - substitute-tier calques (translit / coined-verb / pseudo-term, or any entry
    with tier="substitute"): replaced on screen with a Markdown link whose
    visible text is the correct word and whose URL carries the original calque,
    so the terminal's dotted underline flags the swap and hover/click reveals
    what was replaced. Also recorded as `high` for the model.
  - hint-tier calques (calque-sense / untranslated): left on screen, recorded as
    `low` for the model.

Findings accumulate per message (in md_<message_id>.json across flushes) and on
the final flush are merged into the session pending (<session>.pending.json) for
the UserPromptSubmit injector (calque-hint-inject.py) to feed the model next
turn. Screen-only: the transcript and model context keep the original.

Exempt (no detect, no replace): fenced code, <meta-discussion> zones, inline
`code`, the tool name «Анти-калька», and any message inside a /calque-blocklist
launch turn (its own work naturally names calques). Any error -> emit nothing,
original shows.
"""
import json
import os
import re
import sys
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
BLOCKLIST = CLAUDE_HOME / "calque-blocklist.json"
STATE_DIR = CLAUDE_HOME / "calque_guard"

# A replaced substitute-tier calque is shown as a Markdown link: visible text is
# the correct word, the URL carries the original calque, so the terminal's dotted
# underline flags the swap and hover/click reveals what was replaced. The primary
# endpoint just echoes the word back; the fallback form searches it instead:
# "https://ya.ru/?q={}".
REVEAL_URL = "https://postman-echo.com/get?text={}"

HARD_KINDS = {"translit", "coined-verb", "pseudo-term"}
NAME_GUARD = r"(?<!анти-)"  # «Анти-калька» is the tool name — never touch it
FENCE_RE = re.compile(r"^\s*(```|~~~)")
META_OPEN_RE = re.compile(r"^\s*<meta-discussion>", re.IGNORECASE)
META_CLOSE_RE = re.compile(r"^\s*</meta-discussion>", re.IGNORECASE)
INLINE_SPLIT_RE = re.compile(r"(`[^`]*`)")

# Exemption by structure, not by guessing intent: a message is left untouched only
# when it belongs to a /calque-blocklist launch turn (the skill's own work names
# calques). To quote a calque in any other message, wrap it in `backticks` or a
# <meta-discussion> zone — no keyword exempts a whole message. These two also serve
# find_calque_uses.py, which imports them (single source for exemption 4).
SKILL_CMD_MARK = "<command-name>/calque-blocklist"
ENVELOPE_PREFIXES = ("<", "Base directory for this skill", "Caveat:", "[Request interrupted")


def load_entries():
    """Compiled detectors for every banned entry carrying a `detect` list."""
    data = json.loads(BLOCKLIST.read_text(encoding="utf-8"))
    out = []
    for lemma, e in data.get("banned", {}).items():
        detect = e.get("detect")
        if not detect:
            continue
        tier = e.get("tier") or ("substitute" if e.get("kind") in HARD_KINDS else "hint")
        replace = e.get("replace", [])
        pats = [re.compile(NAME_GUARD + r"\b" + re.escape(stem) + r"[\wА-Яа-яЁё-]*", re.IGNORECASE)
                for stem in detect]
        out.append({
            "lemma": lemma,
            "tier": tier,
            "patterns": pats,
            "repl": replace[0] if (tier == "substitute" and replace) else None,
            "replace": replace,
            "note": e.get("note", ""),
        })
    return out


def _cap(repl, surface):
    return (repl[:1].upper() + repl[1:]) if surface[:1].isupper() else repl


def scan_and_sub(line, entries, high, low):
    """Detect calques in the non-code parts of `line` (recording into high/low)
    and return the display line with substitute-tier calques replaced."""
    parts = INLINE_SPLIT_RE.split(line)
    for i, part in enumerate(parts):
        if part.startswith("`"):  # inline code span — exempt
            continue
        for e in entries:
            def repl_fn(m, e=e):
                surface = m.group(0)
                rec = {"surface": surface, "replace": e["replace"], "note": e["note"]}
                (high if e["tier"] == "substitute" else low)[e["lemma"]] = rec
                if not e["repl"]:
                    return surface
                return f"[{_cap(e['repl'], surface)}]({REVEAL_URL.format(surface)})"
            for pat in e["patterns"]:
                part = pat.sub(repl_fn, part)
        parts[i] = part
    return "".join(parts)


def process(delta, state, entries):
    out = []
    for line in delta.split("\n"):
        if FENCE_RE.match(line):
            state["fence"] = not state["fence"]
            out.append(line)
            continue
        if not state["fence"]:
            if META_OPEN_RE.match(line):
                state["meta"] = True
                out.append(line)
                continue
            if META_CLOSE_RE.match(line):
                state["meta"] = False
                out.append(line)
                continue
        if state["fence"] or state["meta"]:
            out.append(line)
            continue
        out.append(scan_and_sub(line, entries, state["high"], state["low"]))
    return "\n".join(out)


def prose_only(text):
    """Strip exempt regions (fenced code, <meta-discussion> zones, inline `code`)
    from full message text, leaving only bare prose — which IS usage. Same per-line
    structural rules process() applies to the live stream, so the offline scanners
    (find_calque_uses, render_stoplist) exempt exactly what the live hook exempts.
    Whole-message exemption (a /calque-blocklist launch turn) is separate — see
    is_real_user_turn / turn_after_user."""
    fence = meta = False
    out = []
    for line in (text or "").split("\n"):
        if FENCE_RE.match(line):
            fence = not fence
            continue
        if not fence:
            if META_OPEN_RE.match(line):
                meta = True
                continue
            if META_CLOSE_RE.match(line):
                meta = False
                continue
        if fence or meta:
            continue
        parts = INLINE_SPLIT_RE.split(line)
        out.append("".join(p for p in parts if not p.startswith("`")))
    return "\n".join(out)


# --- transcript reading, to detect a /calque-blocklist launch turn ----------

def content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [i["text"] for i in content
                 if isinstance(i, dict) and i.get("type") == "text" and isinstance(i.get("text"), str)]
        return "\n".join(parts)
    return ""


def read_transcript(path):
    objs = []
    try:
        with open(path, "r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    objs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return objs


def is_tool_result_user(obj):
    if obj.get("toolUseResult") is not None:
        return True
    content = obj.get("message", {}).get("content")
    if isinstance(content, list):
        return len(content) > 0 and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def is_real_user_turn(obj):
    """A user record that starts a turn (not a tool result, not a sidechain).
    Both the live check (current_turn_is_skill) and the offline scanners
    (find_calque_uses, render_stoplist) gate turn-tracking on this."""
    if obj.get("type") != "user" or obj.get("isSidechain"):
        return False
    return not is_tool_result_user(obj)


def turn_after_user(skill_turn, user_text):
    """Update the 'inside a /calque-blocklist launch turn' flag for one user turn:
    the command envelope opens it; injected envelopes (skill body, reminders) keep
    it; a genuine user prose message ends it."""
    text = (user_text or "").lstrip()
    if SKILL_CMD_MARK in text:
        return True
    if text.startswith(ENVELOPE_PREFIXES):
        return skill_turn
    return False


def current_turn_is_skill(objs):
    """Whether the latest turn (the one the streaming message answers) is a
    /calque-blocklist launch turn."""
    skill_turn = False
    for obj in objs:
        if not is_real_user_turn(obj):
            continue
        skill_turn = turn_after_user(skill_turn, content_to_text(obj.get("message", {}).get("content")))
    return skill_turn


# --- state / pending --------------------------------------------------------

def _san(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", s or "")


def state_path(message_id):
    return STATE_DIR / f"md_{_san(message_id) or 'nomsg'}.json"


def pending_file(session_id):
    return STATE_DIR / f"{_san(session_id)}.pending.json"


def atomic_write(path, text):
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def merge_pending(session_id, high, low):
    """Merge this message's findings into the session pending. Accumulates across
    the several assistant messages of one turn; the injector resets it next turn."""
    if not session_id or (not high and not low):
        return
    pf = pending_file(session_id)
    data = {"high": {}, "low": {}}
    try:
        existing = json.loads(pf.read_text(encoding="utf-8"))
        data["high"].update(existing.get("high", {}))
        data["low"].update(existing.get("low", {}))
    except (OSError, json.JSONDecodeError):
        pass
    data["high"].update(high)
    data["low"].update(low)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write(pf, json.dumps(data, ensure_ascii=False))
    except OSError:
        pass


def main():
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    delta = payload.get("delta", "")
    message_id = payload.get("message_id") or payload.get("messageId") or ""
    session_id = payload.get("session_id") or payload.get("sessionId") or ""
    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath") or ""
    final = bool(payload.get("final"))

    sf = state_path(message_id)
    fresh = not sf.exists()
    state = {"fence": False, "meta": False, "skip": False, "high": {}, "low": {}}
    try:
        state.update(json.loads(sf.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass

    # First flush: is this message inside a /calque-blocklist launch turn? If so,
    # leave the whole message untouched (no detect, no replace).
    if fresh:
        try:
            if transcript_path and os.path.exists(transcript_path):
                if current_turn_is_skill(read_transcript(transcript_path)):
                    state["skip"] = True
        except Exception:
            pass

    new = delta
    if delta and not state["skip"]:
        new = process(delta, state, load_entries())

    if final:
        if not state["skip"]:
            merge_pending(session_id, state["high"], state["low"])
        try:
            sf.unlink()
        except OSError:
            pass
    else:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write(sf, json.dumps(state, ensure_ascii=False))
        except OSError:
            pass

    if new != delta:
        print(json.dumps({
            "hookSpecificOutput": {"hookEventName": "MessageDisplay", "displayContent": new}
        }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # show the original delta on any failure
        sys.exit(0)
