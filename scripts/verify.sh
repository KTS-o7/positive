#!/usr/bin/env bash
# Verify a Positive edition after the cron finishes.
set -e
LATEST=$(ls -t /opt/positive/data/*.json 2>/dev/null | grep -v latest | head -1)
if [ -z "$LATEST" ]; then
  echo "FAIL: no edition found in data/"
  exit 1
fi
echo "checking: $LATEST"
python3 - "$LATEST" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
n = len(d.get("stories", []))
print(f"  stories: {n}")
assert n == 5, f"expected 5 stories, got {n}"
for s in d["stories"]:
    paras = len(s.get("body", []))
    chars = sum(len(x) for x in s.get("body", []))
    assert 4 <= paras <= 6, f"{s['id']}: {paras} paragraphs (want 4-6)"
    print(f"  {s['id']:32s} {paras} paragraphs  {chars} chars")
sources = [s.get("source", "") for s in d["stories"]]
print(f"  sources: {sources}")
print("OK")
PY
echo ""
echo "checking live site..."
curl -sIk https://positive.shenthar.me/data/latest.json | head -1
echo ""
echo "checking unicode escapes..."
grep -rn '\\u[0-9a-fA-F]\{4\}' /opt/positive/data/ 2>&1 | head -3 || echo "  (clean)"