#!/usr/bin/env python3
"""Publish a source-grounded Positive edition.

The model writes the final micro-retellings. Source selection is mechanical:
each edition draws five previously unused story IDs from data/source_catalog.json
and permanently records them in data/used_sources.json. There is no cycle and
no reuse fallback.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CATALOG = DATA / "source_catalog.json"
USED = DATA / "used_sources.json"
ARCHIVE = ROOT / "archive.json"
IST = timezone(timedelta(hours=5, minutes=30))
MODEL = "MiniMax-M3"

SITE = {
    "name": "Positive",
    "tagline": "Small stories from old wisdom.",
    "base_url": "https://positive.shenthar.me",
    "repo": "https://github.com/KTS-o7/positive",
    "intro": "Five small retellings from India’s living story traditions. Read one, then return to your day a little lighter.",
}

STORY_TOOL = {
    "type": "function",
    "function": {
        "name": "write_retelling",
        "description": "Return one finished source-faithful micro-retelling.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "A concise, warm title of 3–8 words."},
                "paragraphs": {
                    "type": "array", "minItems": 3, "maxItems": 3,
                    "items": {"type": "string", "minLength": 70, "maxLength": 500},
                    "description": "Exactly three short finished paragraphs."},
            },
            "required": ["title", "paragraphs"],
            "additionalProperties": False,
        },
    },
}


def load_env() -> None:
    path = pathlib.Path.home() / ".hermes" / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_json(path: pathlib.Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def atomic_write(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
        temporary = pathlib.Path(f.name)
    temporary.chmod(0o644)
    temporary.replace(path)


def slug(title: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")[:64]


def prompt(source: dict[str, str]) -> str:
    return f"""Write a short, warm Positive retelling of the actual story below.

SOURCE: {source['source']}
EPISODE: {source['title']}
CANONICAL PLOT (preserve its named people/animals, setting, key action, and outcome):
---
{source['story']}
---

Write for someone who wants a quick moment of hope. Use exactly three small
paragraphs, about 35–55 words each. Retell this episode; do not replace it with
a new modern story, an abstract reflection, a Western setting, or a sermon.
Keep its Indian/dharmic world visible through the actual names, places, or
objects already present in the source episode. End with the story's naturally
hopeful or compassionate turn. Do not explain the source, give advice, or add
any text outside write_retelling."""


def generate(source: dict[str, str]) -> dict[str, Any]:
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        raise RuntimeError("MINIMAX_API_KEY unavailable")
    request_body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt(source)}],
        "tools": [STORY_TOOL],
        "tool_choice": {"type": "function", "function": {"name": "write_retelling"}},
        "parallel_tool_calls": False,
        "thinking": {"type": "disabled"},
        "max_completion_tokens": 900,
        "temperature": 0.65,
    }
    request = urllib.request.Request(
        "https://api.minimax.io/v1/chat/completions",
        data=json.dumps(request_body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"MiniMax HTTP {error.code}: {error.read().decode(errors='replace')[:300]}") from error
    choices = payload.get("choices") or []
    calls = ((choices[0] if choices else {}).get("message") or {}).get("tool_calls") or []
    if len(calls) != 1 or calls[0].get("function", {}).get("name") != "write_retelling":
        raise ValueError("M3 did not return the required retelling tool call")
    try:
        return json.loads(calls[0]["function"]["arguments"])
    except (KeyError, json.JSONDecodeError) as error:
        raise ValueError("M3 returned invalid retelling arguments") from error


def story_object(raw: dict[str, Any], source: dict[str, str], edition: str) -> dict[str, Any]:
    if set(raw) != {"title", "paragraphs"} or not isinstance(raw["title"], str):
        raise ValueError("invalid structured retelling")
    title = raw["title"].strip()
    body = raw["paragraphs"]
    if not (3 <= len(title.split()) <= 8 and isinstance(body, list) and len(body) == 3 and all(isinstance(p, str) for p in body)):
        raise ValueError("retelling did not contain a title and three paragraphs")
    body = [p.strip() for p in body]
    if not all(70 <= len(p) <= 500 for p in body):
        raise ValueError("retelling paragraph length is outside the micro-story range")
    return {
        "id": slug(title), "title": title, "source": source["source"],
        "source_url": source["source_url"], "source_id": source["id"],
        "published_at": edition, "body": body,
    }


def select_sources(catalog: list[dict[str, str]], used: set[str]) -> list[dict[str, str]]:
    available = [item for item in catalog if item["id"] not in used]
    if len(available) < 5:
        raise RuntimeError(f"source catalog has only {len(available)} unused episodes; refusing to repeat a story")
    # Random selection among unused entries—not a cyclic/day-indexed rotation.
    return random.SystemRandom().sample(available, 5)


def update_archive(edition: str) -> list[str]:
    old = load_json(ARCHIVE, [])
    return list(dict.fromkeys([edition, *old])) if isinstance(old, list) else [edition]


def commit(edition: str) -> None:
    env = os.environ | {"GIT_AUTHOR_NAME": "Hermes", "GIT_AUTHOR_EMAIL": "hermes@nous.local", "GIT_COMMITTER_NAME": "Hermes", "GIT_COMMITTER_EMAIL": "hermes@nous.local"}
    def run(*cmd: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)
    if run("git", "add", "data", "archive.json", "scripts/build_edition.py").returncode:
        raise RuntimeError("git add failed")
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        return
    result = run("git", "commit", "-m", f"edition {edition} — 5 source-grounded retellings")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git commit failed")
    result = run("git", "push", "origin", "master")
    if result.returncode:
        raise RuntimeError("committed locally but push failed: " + result.stderr.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(IST).strftime("%Y-%m-%d"))
    parser.add_argument("--stage", type=pathlib.Path, help="Generate to this file without publishing")
    parser.add_argument("--publish-stage", type=pathlib.Path, help="Publish a previously staged edition without new LLM calls")
    args = parser.parse_args()
    load_env()

    if args.publish_stage:
        staged = load_json(args.publish_stage, None)
        if not isinstance(staged, dict):
            raise RuntimeError("staged edition is unreadable")
        edition, selected, document = staged["edition"], staged["sources"], staged["document"]
    else:
        catalog = load_json(CATALOG, [])
        if not isinstance(catalog, list):
            raise RuntimeError("source catalog is invalid")
        used_doc = load_json(USED, {"used": []})
        used = {entry["id"] for entry in used_doc.get("used", [])}
        selected = select_sources(catalog, used)
        print(f"Generating {args.date}: five new source-grounded M3 retellings", file=sys.stderr)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            raw = list(pool.map(generate, selected))
        stories = [story_object(item, source, args.date) for item, source in zip(raw, selected)]
        document = {"site": SITE, "edition_date": args.date, "stories": stories}
        edition = args.date
        staged = {"edition": edition, "sources": selected, "document": document}
        if args.stage:
            atomic_write(args.stage, staged)
            print(f"STAGED: {args.stage}", file=sys.stderr)
            return

    used_doc = load_json(USED, {"used": []})
    used = {entry["id"] for entry in used_doc.get("used", [])}
    ids = [source["id"] for source in selected]
    if len(set(ids)) != 5 or any(item in used for item in ids):
        raise RuntimeError("refusing to publish a repeated source episode")
    atomic_write(DATA / f"{edition}.json", document)
    atomic_write(DATA / "latest.json", document)
    atomic_write(ARCHIVE, update_archive(edition))
    used_doc.setdefault("used", []).extend({"id": source["id"], "edition": edition, "source": source["source"]} for source in selected)
    atomic_write(USED, used_doc)
    commit(edition)
    print(f"OK: {edition}; 5 new source-grounded M3 retellings")

if __name__ == "__main__":
    main()
