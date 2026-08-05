#!/usr/bin/env python3
"""Daily Positive edition builder.

Mirrors the Daily Byte architecture: the LLM does one bounded
prose-generation call per story. All mechanical work (file IO, JSON
assembly, git commit, push) is done by this script. The LLM is called
via HTTP (MiniMax-M3 primary, opencode-zen fallback) once per story
in parallel.

Outputs (into OUT_DIR):
  data/<EDITION>.json   today's full digest
  data/latest.json       copy of today (the homepage fetches this)
  archive.json           list of editions, newest-first

No secrets in this repo: API keys are read from env only.
"""

import argparse
import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone


def _load_env():
    """Source ~/.hermes/.env if present, so the script picks up API keys
    when run outside the gateway (e.g. from a manual shell)."""
    env_file = pathlib.Path.home() / ".hermes" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data"
SEED_FILE = OUT_DIR / "stories.json"
ARCHIVE_FILE = ROOT / "archive.json"

SITE_NAME = "Positive"
SITE_TAGLINE = "Five small stories, gently told."
SITE_INTRO = (
    "A handful of short pieces from Hermes, written on slow evenings. "
    "No notifications, no streaks, no list to maintain. Open the page, "
    "read one, close the tab. That is the whole thing."
)
BASE_URL = "https://positive.shenthar.me"
REPO = "https://github.com/KTS-o7/positive"
PARAGRAPHS_PER_STORY = 5
IST = timezone(timedelta(hours=5, minutes=30))

# Seed pool — rotate deterministically by day-of-year so we never repeat
# the same source twice in one edition. Each entry: (source_label,
# seed_prompt). The seed_prompt is the LLM's "what to write about".
# This is the only place the dharmic source is named; the prose itself
# never names it.
SEED_POOL = [
    ("Hermes · after the Katha Upanishad",
     "the chariot allegory: the self is the rider, the body is the chariot, "
     "the intellect the charioteer. write a small piece about a vehicle of "
     "some kind — a bicycle, a boat, a cart — and the one who steers it. "
     "the lesson is not to drive harder, but to know which passenger is "
     "actually in charge."),
    ("Hermes · after the Bhagavad Gita, ch. 2",
     "the settled sage (sthitaprajna): one who is not shaken by sorrow or "
     "pursued by pleasure. write a small piece about someone — a tea-shop "
     "owner, a gardener, a grandfather — who has clearly seen enough that "
     "they no longer chase. what does the morning look like for them?"),
    ("Hermes · after the Mundaka Upanishad",
     "the two birds on one branch: one eats the fruit, the other watches. "
     "the watcher is what you really are. write about a small scene — a "
     "window, a pair of eyes, a long afternoon — where the watcher and "
     "the doer quietly separate."),
    ("Hermes · after Vivekananda",
     "the difference between knowledge and experience. the candle is not "
     "the sun. write about someone who tried to explain something they "
     "knew about but had not lived. what does the gap between those two "
     "look like in their face?"),
    ("Hermes · after the Yoga Sutras",
     "abhyasa and vairagya — practice and letting go. neither alone is "
     "enough. write about a craftsperson — a baker, a writer, a carpenter "
     "— and the way both showing-up and stepping-back are part of the "
     "work."),
    ("Hermes · after the Mahabharata",
     "Kunti asking for repeated sorrow, so she can recognise joy. write "
     "about someone who has learned to be grateful not for the good days "
     "but for the contrast the bad ones gave them. keep it small — a "
     "specific memory, not a sermon."),
    ("Hermes · after the Ramayana",
     "Shabari's berries — she tasted each one first, to make sure it was "
     "sweet, before offering it to Rama. write about the kindness of "
     "filtering — someone who quietly checks their gift before giving "
     "it. keep it concrete: a cup of tea, a plate, a sentence."),
    ("Hermes · after Ramana Maharshi",
     "'Who am I?' — the question that does not expect an answer. write "
     "about a small moment where someone catches themselves mid-action "
     "and asks, very quietly, what they were actually doing. the world "
     "keeps going around them."),
    ("Hermes · after Amma",
     "the embrace as practice. write about someone — a teacher, a bus "
     "conductor, an aunt — whose small repeated kindnesses you can still "
     "feel years later. do not name the embrace; let the reader feel it "
     "in a gesture instead."),
    ("Hermes · after Nisargadatta",
     "'I am not the body, I am not the mind.' the question of what "
     "remains when the story stops. write about a person who is very "
     "old or very tired and the way the things they once cared about "
     "have gently stopped mattering. do not mourn them."),
    ("Hermes · after the Bhagavad Gita, ch. 13",
     "the field and the knower of the field. write about a place — a "
     "garden, a kitchen, a long beach — and the one who walks through "
     "it knowing the place will outlast them. what does that walk look "
     "like?"),
    ("Hermes · after Saraha",
     "the tantric poet's instruction: do not look for the moon in the "
     "reflection. write about a small mistake of looking — someone "
     "chasing the wrong version of a thing they already had. end on a "
     "small quiet turn."),
    ("Hermes · after Patanjali, sutra 1.33",
     "the rain-cloud of dharma: by cultivating friendliness, compassion, "
     "gladness, and indifference, the mind becomes clear. write a small "
     "piece about a person — a nurse, a teacher, a friend — whose four-"
     "mood weather you can feel from across the room."),
    ("Hermes · after the Chandogya Upanishad",
     "'tat tvam asi' — thou art that. write about the moment a parent "
     "sees themselves in a child — not the look, the gesture, the sigh — "
     "and stops trying to fix anything for a moment. the recognition is "
     "the whole piece."),
    ("Hermes · after the Hitopadesha",
     "the mouse who became a lion by remembering he was one. write "
     "about a person who has clearly changed shape — quieter, smaller, "
     "more careful — and yet some old largeness is still visible if you "
     "look. keep it small, do not make them heroic."),
    ("Hermes · after a Zen story often told in Indian form",
     "the monk who said nothing for ten years, then laughed. write "
     "about a long silence between two people — a marriage, a "
     "friendship, a working day — and the small laugh that finally "
     "comes. do not explain the laugh."),
]

VOICE_INSTRUCTION = (
    "Voice rules (do not violate):\n"
    "- Open with a concrete image: a place, a person, an object, a time of day. "
    "Never 'Once upon a time', never 'Have you ever felt...'.\n"
    "- Specificity over generality. 'A small temple at the edge of a village' "
    "— not 'a temple in India'. 'Thirty-eight years' — not 'decades'.\n"
    "- Close on a quiet invitation. Not a moral, not a CTA. A sentence the "
    "reader can carry.\n"
    "- No second-person coaching. Don't tell the reader what to do.\n"
    "- No affirmations, no slogans, no 'Today I want to remind you...'.\n"
    "- No emojis in the body.\n"
    "- Do NOT name the source material in the prose. The reader should feel "
    "the seed, not be told about it.\n"
    "- Plain modern English. No Sanskrit terms. No emoji.\n"
)

# System prompt sets a tight role: writer at a desk, not assistant.
# Without this, M3 dumps task analysis into the content.
SYSTEM_PROMPT = (
    "You are a writer working on a daily short-story column. You will be "
    "given a SEED — a small concrete scene or situation to write about. "
    "You write the piece and nothing else. No preamble. No planning. No "
    "restating the prompt. No 'The user wants...'. No 'Let me think...'. "
    "No title-of-section labels. No notes to the editor. Just the title "
    "and the 5 paragraphs of prose. Begin your output directly with the "
    "title (3-7 words)."
)

USER_PROMPT = (
    "SEED: {seed}\n\n"
    "{voice}\n\n"
    "Write ONE short piece. Exactly 5 paragraphs, 2-4 sentences each. "
    "~250-400 words total.\n\n"
    "Start your output with the title. Then the 5 paragraphs, separated "
    "by a single blank line. Nothing else."
)


# --------------------------------------------------------------------------
# LLM client (mirrors Daily Byte's pattern)
# --------------------------------------------------------------------------
def _llm_endpoint():
    """Return (url, key, model)."""
    if os.environ.get("MINIMAX_API_KEY"):
        return "https://api.minimax.io/v1/chat/completions", os.environ["MINIMAX_API_KEY"], "MiniMax-M3"
    if os.environ.get("OPENCODE_ZEN_API_KEY"):
        return "https://opencode.ai/zen/v1/chat/completions", os.environ["OPENCODE_ZEN_API_KEY"], "deepseek-v4-flash-free"
    if os.environ.get("KIMI_API_KEY"):
        return "https://api.moonshot.cn/v1/chat/completions", os.environ["KIMI_API_KEY"], "moonshot-v1-8k"
    raise RuntimeError("no LLM API key in env (MINIMAX_API_KEY, OPENCODE_ZEN_API_KEY, or KIMI_API_KEY required)")


def _llm_complete(prompt, max_tokens=900):
    url, key, model = _llm_endpoint()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        # M3 emits a <think>... block by default; this param keeps the
        # reasoning in a separate field so prose stays clean.
        "reasoning_split": True,
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                payload = json.load(r)
            msg = payload["choices"][0].get("message", {})
            # With reasoning_split, prose is in 'content' and reasoning
            # is in 'reasoning_content'. Without it, both may be merged.
            txt = (msg.get("content") or msg.get("reasoning_content") or "").strip()
            if txt:
                return txt
            raise RuntimeError(f"empty completion (msg keys={list(msg.keys())})")
        except (urllib.error.URLError, KeyError, RuntimeError, json.JSONDecodeError) as e:
            print(f"  llm attempt {attempt+1} failed: {e}", file=sys.stderr)
            time.sleep(2 + attempt * 2)
    raise RuntimeError("all LLM attempts failed")


def parse_piece(text):
    """Split raw LLM output into (title, [paragraphs]). Strips any
    leaked <think>...</think> block (M3 sometimes still leaks despite
    reasoning_split) and any leading labels / planning preamble."""
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^(Title|Here'?s?|Here is|Note:|Output:)\s*", "", text, flags=re.IGNORECASE).strip()
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        raise RuntimeError("LLM returned empty prose")
    title = lines[0].strip().strip("\"'`").strip()
    title = re.sub(r"^\*+|\*+$", "", title).strip()
    # Heuristic: if the "title" is too long, it's probably M3 dumping
    # the prompt rephrased. Discard the first paragraph if so.
    if len(title) > 120:
        # Treat the whole thing as one block and skip the long first line
        lines = lines[1:]
        # Try to find the first short-ish line as title
        for i, ln in enumerate(lines):
            if 3 <= len(ln.split()) <= 12:
                title = ln.strip().strip("\"'`").strip()
                lines = lines[i+1:]
                break
        else:
            title = "A Quiet Piece"
    body = []
    buf = []
    for ln in lines:
        if ln.strip() == "":
            if buf:
                body.append(" ".join(buf).strip())
                buf = []
        else:
            buf.append(ln.strip())
    if buf:
        body.append(" ".join(buf).strip())
    # If M3 returned one giant blob instead of paragraphs, split on
    # sentence boundaries.
    if len(body) == 1 and len(body[0]) > 1200:
        sentences = re.split(r"(?<=[.!?])\s+", body[0])
        body, buf = [], []
        per = max(180, len(sentences) // 5)
        for s in sentences:
            buf.append(s)
            if sum(len(x) for x in buf) >= per and len(body) < 4:
                body.append(" ".join(buf))
                buf = []
        if buf:
            body.append(" ".join(buf))
    return title, body


def slugify(title):
    import re
    s = re.sub(r"[^a-z0-9\s-]", "", title.lower())
    return re.sub(r"[\s-]+", "-", s).strip("-")[:48] or "story"


def write_story(seed_idx, edition):
    src, seed = SEED_POOL[seed_idx % len(SEED_POOL)]
    prompt = USER_PROMPT.format(seed=seed, voice=VOICE_INSTRUCTION)
    raw = _llm_complete(prompt)
    title, body = parse_piece(raw)
    return {
        "id": slugify(title),
        "title": title,
        "source": src,
        "published_at": edition,
        "body": body,
    }


# --------------------------------------------------------------------------
# Archive + commit
# --------------------------------------------------------------------------
def update_archive(edition):
    if ARCHIVE_FILE.exists():
        try:
            arr = json.load(open(ARCHIVE_FILE))
            if not isinstance(arr, list):
                arr = []
        except json.JSONDecodeError:
            arr = []
    else:
        arr = []
    if edition not in arr:
        arr.insert(0, edition)
    arr = list(dict.fromkeys(arr))  # dedupe, preserve order
    ARCHIVE_FILE.write_text(json.dumps(arr, indent=2) + "\n")


def git_commit_push(edition):
    msg = f"edition {edition} — 5 stories"
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Hermes"
    env["GIT_AUTHOR_EMAIL"] = "hermes@nous.local"
    env["GIT_COMMITTER_NAME"] = "Hermes"
    env["GIT_COMMITTER_EMAIL"] = "hermes@nous.local"
    for cmd in (
        ["git", "-C", str(ROOT), "add", "-A"],
        ["git", "-C", str(ROOT), "commit", "-m", msg],
        ["git", "-C", str(ROOT), "push", "origin", "master"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        print(r.stdout.strip(), file=sys.stderr)
        if r.returncode != 0 and cmd[2] != "push":
            print(r.stderr.strip(), file=sys.stderr)
            raise RuntimeError(f"git failed: {' '.join(cmd)}")
    return True


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    _load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="edition date (default: today IST)")
    args = ap.parse_args()
    edition = args.date or datetime.now(IST).strftime("%Y-%m-%d")
    print(f"== building {edition} ==", file=sys.stderr)

    # Deterministic rotation: skip N seeds based on day-of-year so the same
    # source never lands twice in one edition and the rotation moves
    # forward each day.
    doy = int(datetime.strptime(edition, "%Y-%m-%d").strftime("%j"))
    offsets = [(doy + i * 3) % len(SEED_POOL) for i in range(5)]
    # Verify no duplicate
    assert len(set(offsets)) == 5, f"seed rotation collided: {offsets}"

    print(f"  seed offsets: {offsets}", file=sys.stderr)

    # Parallel LLM calls — one per story
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        stories = list(ex.map(lambda i: write_story(offsets[i], edition), range(5)))

    site = {
        "name": SITE_NAME,
        "tagline": SITE_TAGLINE,
        "base_url": BASE_URL,
        "repo": REPO,
        "intro": SITE_INTRO,
    }
    digest = {"site": site, "edition_date": edition, "stories": stories}

    # Validate
    assert len(stories) == 5
    for s in stories:
        assert 4 <= len(s["body"]) <= 6, f"{s['id']}: {len(s['body'])} paragraphs"

    # Write artifacts
    OUT_DIR.mkdir(exist_ok=True)
    edition_path = OUT_DIR / f"{edition}.json"
    edition_path.write_text(json.dumps(digest, indent=2, ensure_ascii=False))
    (OUT_DIR / "latest.json").write_text(json.dumps(digest, indent=2, ensure_ascii=False))
    update_archive(edition)
    print(f"  wrote {edition_path}", file=sys.stderr)
    print(f"  wrote {OUT_DIR / 'latest.json'}", file=sys.stderr)
    print(f"  updated {ARCHIVE_FILE}", file=sys.stderr)

    git_commit_push(edition)
    print("OK")


if __name__ == "__main__":
    main()