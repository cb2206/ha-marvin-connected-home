"""mitmproxy addon — log the Marvin app's API calls in a redacted, readable form.

The raw ``.mitm`` file carries live bearer tokens and must never be committed.
This writes a parallel JSONL with credentials stripped, which *is* safe to read,
quote in API.md, and paste into a diff.

Usage:
    mitmdump -s scripts/capture/marvin_addon.py -w capture.mitm --set jsonl=capture.jsonl
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mitmproxy import ctx, http

# Hosts worth recording. Deliberately broader than the one API host: an
# unexplored feature may well talk to a Marvin subdomain nobody has seen yet,
# and a filter pinned to `azapi` would drop it without trace. Everything else
# (Google, telemetry, CDNs) is noise that would bury the real traffic.
INTERESTING_HOSTS = ("marvin.com", "service.signalr.net", "b2clogin.com")

# Anything that changes state. Surfaced loudly in the console so the operator
# can see their tap land without grepping. This is a *highlight*, not a filter
# -- every request to an interesting host is recorded either way, which matters
# when mapping a feature whose endpoints are not yet known.
ACTION_RE = re.compile(
    r"/(reboot|recalib|calib|performota|commands|setconfig|reset|preference"
    r"|schedule|automation|away|group|rename|create|update|delete|add|remove)",
    re.IGNORECASE,
)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

SECRET_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "ocp-apim-subscription-key"}
SECRET_KEYS = re.compile(
    r"token|password|secret|client_secret|code_verifier|assertion|signature", re.IGNORECASE
)

MAX_BODY = 20_000


class MarvinCapture:
    def __init__(self) -> None:
        self.path: Path | None = None
        self.count = 0

    def load(self, loader: Any) -> None:
        loader.add_option(
            "jsonl", str, "", "Path to write the redacted JSONL transcript to."
        )

    def configure(self, updates: set[str]) -> None:
        if "jsonl" in updates and ctx.options.jsonl:
            self.path = Path(ctx.options.jsonl)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            ctx.log.info(f"[marvin] redacted transcript -> {self.path}")

    def response(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        if not any(h in host for h in INTERESTING_HOSTS):
            return

        self._write(
            {
                "kind": "http",
                "time": flow.request.timestamp_start,
                "method": flow.request.method,
                "host": host,
                "path": flow.request.path,
                "request_headers": _redact_headers(flow.request.headers),
                "request_body": _redact_body(flow.request.get_text(strict=False)),
                "status": flow.response.status_code,
                "response_body": _redact_body(flow.response.get_text(strict=False)),
            }
        )

        interesting = (
            flow.request.method in WRITE_METHODS or ACTION_RE.search(flow.request.path)
        )
        marker = " <<<" if interesting else ""
        body = _redact_body(flow.request.get_text(strict=False)) or ""
        ctx.log.info(
            f"[marvin] {flow.request.method} {flow.request.path} "
            f"-> {flow.response.status_code} {body[:300]}{marker}"
        )

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        """Record SignalR frames.

        The HTTP hook only sees the 101 upgrade, so without this every push the
        hub sends is invisible. That matters when mapping a feature whose state
        arrives over the socket rather than in a REST response -- which, given
        `AssetUpdated` already works that way, is the likely shape for
        schedules and automations too.
        """
        if flow.websocket is None:
            return
        message = flow.websocket.messages[-1]
        text = message.text if message.is_text else f"<{len(message.content)} binary bytes>"

        # SignalR delimits frames with 0x1e and double-encodes arguments[0];
        # left as-is here so the analyser sees exactly what came over the wire.
        self._write(
            {
                "kind": "websocket",
                "time": message.timestamp,
                "host": flow.request.pretty_host,
                "path": flow.request.path,
                "direction": "client->server" if message.from_client else "server->client",
                "message": _redact_body(text.replace("\x1e", "")),
            }
        )

    def _write(self, record: dict[str, Any]) -> None:
        self.count += 1
        if self.path is not None:
            with self.path.open("a") as fh:
                fh.write(json.dumps(record) + "\n")


def _redact_headers(headers: Any) -> dict[str, str]:
    return {
        k: ("<redacted>" if k.lower() in SECRET_HEADERS else v) for k, v in headers.items()
    }


def _redact_body(text: str | None) -> str | None:
    """Strip credentials but keep structure — the shape is the whole point."""
    if not text:
        return text
    if len(text) > MAX_BODY:
        text = text[:MAX_BODY] + f"...<truncated {len(text)} bytes>"
    try:
        return json.dumps(_redact_json(json.loads(text)))
    except (ValueError, TypeError):
        # Form-encoded (the B2C token calls) or opaque — redact by key name.
        return re.sub(
            r"((?:access_|refresh_|id_)?token|password|code|client_secret)=[^&\s]+",
            r"\1=<redacted>",
            text,
        )


def _redact_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if SECRET_KEYS.search(k) else _redact_json(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_json(v) for v in obj]
    return obj


addons = [MarvinCapture()]
