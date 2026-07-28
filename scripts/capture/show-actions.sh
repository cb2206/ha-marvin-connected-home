#!/usr/bin/env bash
#
# Print the device-action requests out of a capture, pretty-printed.
# Everything else (auth, house polls, SignalR) is filtered out.
#
set -euo pipefail
JSONL="${1:-captures/marvin.jsonl}"

python3 - "$JSONL" <<'EOF'
import json, re, sys

ACTION = re.compile(r"/(reboot|recalib|calib|performota|commands|setconfig|reset)", re.I)

for line in open(sys.argv[1]):
    r = json.loads(line)
    if not ACTION.search(r["path"]):
        continue
    print(f"=== {r['method']} https://{r['host']}{r['path']}  -> {r['status']}")
    for key in ("request_body", "response_body"):
        body = r.get(key)
        if not body:
            continue
        try:
            body = json.dumps(json.loads(body), indent=2)
        except ValueError:
            pass
        print(f"  {key}: {body}")
    print()
EOF
