#!/usr/bin/env python3
"""Summarise a capture, highlighting what is *not* already documented.

`show-actions.sh` answers "did my tap land?" for an endpoint you already expect.
This answers the exploration question instead: "what did the app just do that
API.md has never seen?"

Endpoint paths are matched against API.md by their static segments, so
`/devices/gen2/reset/reboot/eval3-xyz` matches a documented
`/devices/gen2/reset/reboot/{internalDeviceId}`. Anything left over is new.

    python3 scripts/capture/analyse.py [capture.jsonl] [--since N] [--all]

    --since N   ignore the first N records (skip a previous session)
    --all       include documented endpoints too, not just novel ones
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
API_DOC = REPO / "API.md"

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Path segments that are values, not route structure: ids, uuids, and the
# {placeholders} API.md uses for them.
VARIABLE = re.compile(
    r"^(\{.*\}|[0-9]+|(House|Asset|Device|Group)_.*|eval3-.*"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


# The API base the app prefixes to every call. API.md documents paths relative
# to it, so it has to come off before the two can be compared.
BASE_PREFIX = re.compile(r"^/mch-prd/[0-9.]+")

# Auth and key discovery. Real traffic, but Azure AD B2C's surface rather than
# Marvin's, and already described in API.md's auth section rather than as
# endpoints -- so counting them as "undocumented" is just noise.
IGNORED_HOSTS = ("b2clogin.com",)


def skeleton(path: str) -> str:
    """Reduce a path to its route structure, dropping ids and the query string."""
    path = BASE_PREFIX.sub("", path.split("?")[0])
    return "/".join("*" if VARIABLE.match(seg) else seg for seg in path.split("/"))


def is_documented(path: str, known: set[str]) -> bool:
    """True if `path` matches a documented route.

    Suffix comparison, because API.md is inconsistent about whether it writes a
    path with the version segment (`/v1.1/messages/negotiate`) or without.
    """
    shape = skeleton(path)
    return any(shape == k or shape.endswith(k) for k in known if k)


def documented() -> set[str]:
    if not API_DOC.exists():
        return set()
    text = API_DOC.read_text()
    paths = set()
    # Three spellings appear in API.md: `POST /foo/{id}` in fenced blocks,
    # `| GET | `/users` | …` in tables, and prose. The backtick and pipe
    # between verb and path are both optional.
    for match in re.finditer(
        r"(?:GET|POST|PUT|PATCH|DELETE)\s*\|?\s*`?(/[^\s`|)]+)", text
    ):
        paths.add(skeleton(match.group(1)))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", nargs="?", default="captures/marvin.jsonl")
    parser.add_argument("--since", type=int, default=0)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    source = Path(args.jsonl)
    if not source.exists():
        print(f"No capture at {source}", file=sys.stderr)
        return 1

    records = [json.loads(line) for line in source.open()][args.since :]
    http = [
        r
        for r in records
        if r.get("kind", "http") == "http"
        and not any(h in r.get("host", "") for h in IGNORED_HOSTS)
    ]
    sockets = [r for r in records if r.get("kind") == "websocket"]

    known = documented()
    novel = [r for r in http if not is_documented(r["path"], known)]

    print(f"{len(records)} records since index {args.since} "
          f"({len(http)} HTTP, {len(sockets)} WebSocket)")
    print(f"{len(novel)} HTTP request(s) hit endpoints not documented in API.md\n")

    shown = http if args.all else novel
    if not shown:
        print("Nothing new. Either the feature reuses known endpoints "
              "(check the bodies with --all) or the taps did not register.")
    else:
        for record in shown:
            flag = "WRITE" if record["method"] in WRITE_METHODS else "read "
            new = "" if is_documented(record["path"], known) else "  [NEW ENDPOINT]"
            print(f"=== {flag} {record['method']} {record['path']} "
                  f"-> {record['status']}{new}")
            for key in ("request_body", "response_body"):
                body = record.get(key)
                if not body or body in ("{}", '""'):
                    continue
                try:
                    body = json.dumps(json.loads(body), indent=2)
                except (ValueError, TypeError):
                    pass
                print(f"  {key}:")
                for line in str(body).splitlines()[:40]:
                    print(f"    {line}")
            print()

    if sockets:
        print("--- SignalR frames ---")
        targets: Counter[str] = Counter()
        for record in sockets:
            message = record.get("message") or ""
            match = re.search(r'"target"\s*:\s*"([^"]+)"', message)
            targets[match.group(1) if match else f"({record['direction']}, no target)"] += 1
        for target, count in targets.most_common():
            print(f"  {count:4}  {target}")
        print("\nFull frames are in the capture; grep by target to read one.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
