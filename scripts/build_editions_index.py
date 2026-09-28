#!/usr/bin/env python3
"""Write data/editions.json — every published edition (newest first) with its story titles.

The homepage lists these while Positive is paused. Re-run after publishing an edition.
"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

editions = []
for date in json.loads((ROOT / "archive.json").read_text()):
    path = DATA / f"{date}.json"
    if not path.exists():
        continue
    d = json.loads(path.read_text())
    editions.append({
        "date": date,
        "stories": [{"id": s.get("id", ""), "title": s.get("title", ""), "source": s.get("source", "")}
                    for s in d.get("stories", [])],
    })
(DATA / "editions.json").write_text(json.dumps(editions, indent=2, ensure_ascii=False) + "\n")
print(f"wrote data/editions.json ({len(editions)} editions, {sum(len(e['stories']) for e in editions)} stories)")
