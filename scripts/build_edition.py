#!/usr/bin/env python3
"""Build one structured Positive edition.

Every story is a *forced MiniMax function call*, never free-form response
parsing. The model returns arguments to emit_story; this program validates all
five results before atomically updating the live JSON files.

Safety invariant: if a single model call, JSON parse, editorial validation, or
git operation fails, data/latest.json is left untouched.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SEEDS_PATH = DATA_DIR / "seeds.json"
ARCHIVE_PATH = ROOT / "archive.json"
IST = timezone(timedelta(hours=5, minutes=30))
MODEL = "MiniMax-M3"
MAX_WORKERS = 5

SITE = {
    "name": "Positive",
    "tagline": "Five small stories, gently told.",
    "base_url": "https://positive.shenthar.me",
    "repo": "https://github.com/KTS-o7/positive",
    "intro": (
        "A handful of short pieces from Hermes, written on slow evenings. "
        "No notifications, no streaks, no list to maintain. Open the page, "
        "read one, close the tab. That is the whole thing."
    ),
}

# This is the exact structured object returned by M3 as tool arguments.
# `strict` is requested from the provider, then we validate again locally.
EMIT_STORY_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_story",
        "description": "Return exactly one publishable Positive story as structured data.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "A plain, evocative 3-to-7-word title. Not a question or slogan.",
                },
                "paragraphs": {
                    "type": "array",
                    "description": "Exactly five prose paragraphs in plain English.",
                    "minItems": 5,
                    "maxItems": 5,
                    "items": {"type": "string", "minLength": 120, "maxLength": 1000},
                },
            },
            "required": ["title", "paragraphs"],
            "additionalProperties": False,
        },
    },
}

BANNED_TEXT = re.compile(
    r"<think|the user wants|let me (?:think|draft|analy[sz]e)|"
    r"thinking process|here(?:'s| is) (?:the|a) (?:story|piece)|"
    r"as an ai|title:\s*$|paragraph \d",
    re.IGNORECASE,
)


def load_dotenv() -> None:
    """Load the local Hermes env file without exporting or logging secrets."""
    env_file = pathlib.Path.home() / ".hermes" / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def slugify(title: str) -> str:
    value = re.sub(r"[^a-z0-9\s-]", "", title.lower())
    value = re.sub(r"[\s-]+", "-", value).strip("-")
    return value[:64] or "quiet-piece"


def build_prompt(seed: dict[str, str]) -> str:
    return f"""Write one original short story for Positive, a quiet daily reading site.

SOURCE NOTE — use its underlying idea, but do not name, quote, or paraphrase it:
---
{seed['seed']}
---

Editorial voice:
- Begin in a concrete world: a person, object, place, or time of day.
- Use plain modern English. No Sanskrit terms, devotional slogans, emojis,
  second-person coaching, or overt moral.
- Do not say 'the reader', 'the user', 'this story', or explain your process.
- Let the final sentence land quietly rather than teaching a lesson.
- Write five distinct paragraphs, each 2–4 sentences; the complete piece should
  be roughly 250–450 words.

Call emit_story now. Its arguments must contain only the title and five finished
paragraphs. Do not put commentary in any field."""


def call_model(seed: dict[str, str]) -> dict[str, Any]:
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY is unavailable")

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": build_prompt(seed)}],
        "tools": [EMIT_STORY_TOOL],
        "tool_choice": {"type": "function", "function": {"name": "emit_story"}},
        "parallel_tool_calls": False,
        # Critical: reasoning_split only changes formatting. This switch stops
        # M3 from consuming the story budget in chain-of-thought generation.
        "thinking": {"type": "disabled"},
        "reasoning_split": True,
        # Official current field; enough for five short paragraphs, not a
        # creative ceiling the model has to reason within.
        "max_completion_tokens": 1400,
        "temperature": 0.75,
    }
    request = urllib.request.Request(
        "https://api.minimax.io/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"MiniMax HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MiniMax network error: {exc.reason}") from exc

    choices = result.get("choices") or []
    if len(choices) != 1:
        raise ValueError("MiniMax returned no single choice")
    message = choices[0].get("message") or {}
    calls = message.get("tool_calls") or []
    if len(calls) != 1:
        raise ValueError(f"expected one forced tool call, got {len(calls)}")
    call = calls[0].get("function") or {}
    if call.get("name") != "emit_story":
        raise ValueError(f"unexpected function: {call.get('name')!r}")
    try:
        args = json.loads(call["arguments"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ValueError("tool arguments are not valid JSON") from exc
    return args


def validate_story(args: dict[str, Any], seed: dict[str, str], edition: str) -> dict[str, Any]:
    if set(args) != {"title", "paragraphs"}:
        raise ValueError(f"unexpected story fields: {sorted(args)}")
    title = args["title"]
    paragraphs = args["paragraphs"]
    if not isinstance(title, str) or not isinstance(paragraphs, list):
        raise ValueError("title/paragraphs types are invalid")
    title = title.strip()
    words = title.split()
    if not (3 <= len(words) <= 7) or len(title) > 80 or title.endswith(("?", "!", ".")):
        raise ValueError(f"invalid title: {title!r}")
    if len(paragraphs) != 5 or not all(isinstance(p, str) for p in paragraphs):
        raise ValueError("story does not contain exactly five string paragraphs")
    body = [p.strip() for p in paragraphs]
    lengths = [len(p) for p in body]
    if not all(120 <= length <= 1000 for length in lengths):
        raise ValueError(f"paragraph lengths outside range: {lengths}")
    joined = "\n".join([title, *body])
    if BANNED_TEXT.search(joined):
        raise ValueError("story contains planning/meta text")
    if not (1200 <= len(joined) <= 4500):
        raise ValueError(f"story length outside range: {len(joined)}")
    return {
        "id": slugify(title),
        "title": title,
        "source": seed["source"],
        "published_at": edition,
        "body": body,
    }


def atomic_json_write(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(encoded)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = pathlib.Path(tmp.name)
    temp_path.replace(path)


def update_archive(edition: str) -> list[str]:
    try:
        archive = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
        if not isinstance(archive, list):
            archive = []
    except (FileNotFoundError, json.JSONDecodeError):
        archive = []
    return list(dict.fromkeys([edition, *archive]))


def git_commit_and_push(edition: str) -> None:
    env = os.environ | {
        "GIT_AUTHOR_NAME": "Hermes",
        "GIT_AUTHOR_EMAIL": "hermes@nous.local",
        "GIT_COMMITTER_NAME": "Hermes",
        "GIT_COMMITTER_EMAIL": "hermes@nous.local",
    }
    def run(*command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, env=env)

    staged = run("git", "add", "data", "archive.json", "assets/app.js", "scripts/build_edition.py")
    if staged.returncode:
        raise RuntimeError(staged.stderr.strip() or "git add failed")
    changed = run("git", "diff", "--cached", "--quiet")
    if changed.returncode == 0:
        return
    committed = run("git", "commit", "-m", f"edition {edition} — 5 structured stories")
    if committed.returncode:
        raise RuntimeError(committed.stderr.strip() or "git commit failed")
    pushed = run("git", "push", "origin", "master")
    if pushed.returncode:
        raise RuntimeError(
            "edition committed locally but push failed: " + (pushed.stderr.strip() or "unknown git error")
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Edition date (YYYY-MM-DD); default: today in IST")
    parser.add_argument("--no-push", action="store_true", help="Generate and validate only; do not write or publish")
    args = parser.parse_args()
    load_dotenv()

    edition = args.date or datetime.now(IST).strftime("%Y-%m-%d")
    try:
        datetime.strptime(edition, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("--date must be YYYY-MM-DD") from exc

    seeds = json.loads(SEEDS_PATH.read_text(encoding="utf-8")).get("seeds", [])
    if len(seeds) < 5:
        raise SystemExit("data/seeds.json needs at least five seeds")
    day = int(datetime.strptime(edition, "%Y-%m-%d").strftime("%j"))
    selected = [seeds[(day + n * 3) % len(seeds)] for n in range(5)]
    if len({seed["id"] for seed in selected}) != 5:
        raise SystemExit("seed selection produced duplicates")

    print(f"Building {edition}: 5 forced structured MiniMax calls", file=sys.stderr)
    # One API call per story. No recursive agent loop, self-review pass, or
    # retry cascade. A failed call aborts publication and preserves live data.
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        raw = list(pool.map(call_model, selected))
    stories = [validate_story(item, seed, edition) for item, seed in zip(raw, selected)]
    if len({story["id"] for story in stories}) != 5:
        raise ValueError("generated duplicate story titles")

    edition_doc = {"site": SITE, "edition_date": edition, "stories": stories}
    if args.no_push:
        print(json.dumps(edition_doc, ensure_ascii=False, indent=2))
        print("VALIDATED_ONLY", file=sys.stderr)
        return

    # All generation/validation completed before this point. Publication is
    # intentionally last, so a bad partial result cannot alter the homepage.
    atomic_json_write(DATA_DIR / f"{edition}.json", edition_doc)
    atomic_json_write(DATA_DIR / "latest.json", edition_doc)
    atomic_json_write(ARCHIVE_PATH, update_archive(edition))
    git_commit_and_push(edition)
    print(f"OK: {edition}; 5 validated structured stories")


if __name__ == "__main__":
    main()
