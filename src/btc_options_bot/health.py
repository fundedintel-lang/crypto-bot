from __future__ import annotations

import hmac
import html
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import BotConfig
from .engine import BotEngine
from .utils import json_dumps

logger = logging.getLogger("btc_options_bot.health")


class StatusHandler(BaseHTTPRequestHandler):
    engine: BotEngine
    config: BotConfig
    server_version = "BTCBotHealth/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("http %s - %s", self.address_string(), format % args)

    def _send(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json_dumps(payload).encode("utf-8")
        self._send(status, body, content_type="application/json; charset=utf-8")

    def _authorized(self) -> bool:
        if not self.config.web_token:
            return False
        supplied = self.headers.get("X-Web-Token", "")
        if not supplied:
            parsed = urllib.parse.urlparse(self.path)
            supplied = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        return hmac.compare_digest(supplied, self.config.web_token)

    def do_HEAD(self) -> None:
        self._json({"ok": True})

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in {"/healthz", "/health"}:
            status = self.engine.status()
            healthy = not self.engine.stopped and status["consecutive_errors"] < 20
            self._json(
                {
                    "ok": healthy,
                    "mode": status["mode"],
                    "last_tick": status["last_tick"],
                    "paused": status["paused"],
                    "kill_requested": status["kill_requested"],
                },
                200 if healthy else 503,
            )
            return
        if path in {"/api/status", "/status.json"}:
            self._json(self.engine.status())
            return
        if path == "/favicon.ico":
            self._send(204, b"", content_type="image/x-icon")
            return
        if path == "/":
            self._send(200, self._dashboard().encode("utf-8"), content_type="text/html; charset=utf-8")
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path not in {"/api/pause", "/api/resume", "/api/kill"}:
            self._json({"error": "not_found"}, 404)
            return
        if not self._authorized():
            self._json(
                {"error": "web_controls_disabled", "hint": "Set WEB_TOKEN and send X-Web-Token"},
                403,
            )
            return
        if path == "/api/pause":
            self.config.pause_file.parent.mkdir(parents=True, exist_ok=True)
            self.config.pause_file.touch()
            message = "new entries paused"
        elif path == "/api/resume":
            self.config.pause_file.unlink(missing_ok=True)
            self.engine.state.set_runtime("safety_halt", None)
            message = "pause cleared"
        else:
            self.config.kill_file.parent.mkdir(parents=True, exist_ok=True)
            self.config.kill_file.touch()
            message = "kill switch requested; active positions will be closed"
        self.engine.state.add_event("WARNING", "Web control", message, {"path": path})
        self._json({"ok": True, "message": message, "status": self.engine.status()})

    def _dashboard(self) -> str:
        status = self.engine.status()
        active = status["active_cycles"]
        events = status["recent_events"][:12]
        rows = []
        for cycle in active:
            rows.append(
                "<tr>"
                f"<td>{html.escape(cycle['cycle_id'])}</td>"
                f"<td>{html.escape(cycle['status'])}</td>"
                f"<td>{html.escape(cycle['direction'])}</td>"
                f"<td>{html.escape(cycle['product_symbol'])}</td>"
                f"<td>{cycle['filled_quantity']}</td>"
                f"<td>{html.escape(cycle['entry_spot'])}</td>"
                f"<td>{html.escape(cycle['target_price'])}</td>"
                f"<td>{html.escape(cycle['exit_reason'] or '-')}</td>"
                "</tr>"
            )
        if not rows:
            rows.append("<tr><td colspan='8' class='empty'>No active cycle</td></tr>")
        event_rows = "".join(
            "<li><span class='level'>"
            + html.escape(item["level"])
            + "</span> "
            + html.escape(item["title"])
            + " — "
            + html.escape(item["message"])
            + "</li>"
            for item in events
        )
        next_event = status["next_event"]
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>BTC Options Bot</title>
<style>
:root {{ color-scheme: dark; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }}
body {{ margin: 0; background: #0b1020; color: #e8ecf8; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 28px; }}
h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
.sub {{ color: #9ca8c7; margin-bottom: 24px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 12px; }}
.card {{ background: #151d38; border: 1px solid #28345f; border-radius: 12px; padding: 14px; }}
.label {{ font-size: .75rem; color: #9ca8c7; text-transform: uppercase; letter-spacing: .08em; }}
.value {{ font-size: 1.1rem; margin-top: 6px; word-break: break-word; }}
.good {{ color: #73e2a7; }} .warn {{ color: #ffd36e; }} .bad {{ color: #ff8b8b; }}
section {{ margin-top: 24px; background: #111832; border: 1px solid #28345f; border-radius: 12px; padding: 16px; }}
table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
th,td {{ text-align: left; padding: 9px; border-bottom: 1px solid #263051; }}
th {{ color: #9ca8c7; font-size: .75rem; text-transform: uppercase; }}
.empty {{ color: #9ca8c7; text-align: center; padding: 22px; }}
ul {{ padding-left: 18px; }} li {{ margin: 7px 0; }} .level {{ color: #73e2a7; font-weight: 700; }}
code {{ color: #b9c8ff; }}
</style>
</head>
<body><main>
<h1>BTC Options Bot</h1>
<div class="sub">Exact 1h EMA21 • 18:30 / 01:00 / 17:20 IST • 2% + 2% • ±1500 BTC points • no SL</div>
<div class="cards">
  <div class="card"><div class="label">Mode</div><div class="value">{html.escape(status['mode'].upper())} / {html.escape(status['venue'])}</div></div>
  <div class="card"><div class="label">State</div><div class="value {'bad' if status['kill_requested'] else 'warn' if status['paused'] else 'good'}">{"KILL" if status['kill_requested'] else "PAUSED" if status['paused'] else "RUNNING"}</div></div>
  <div class="card"><div class="label">IST time</div><div class="value">{html.escape(status['now_ist'])}</div></div>
  <div class="card"><div class="label">Last tick</div><div class="value">{html.escape(status['last_tick'] or '-')}</div></div>
  <div class="card"><div class="label">Next event</div><div class="value">{html.escape(next_event['name'])}<br><small>{html.escape(next_event['at'])}</small></div></div>
  <div class="card"><div class="label">Errors</div><div class="value {'bad' if status['last_error'] else 'good'}">{html.escape(status['last_error'] or 'none')}</div></div>
</div>
<section><h2>Active cycles</h2><table>
<thead><tr><th>Cycle</th><th>Status</th><th>Side</th><th>Option</th><th>Qty</th><th>BTC entry</th><th>Target</th><th>Exit reason</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></section>
<section><h2>Recent events</h2><ul>{event_rows or '<li>No events yet</li>'}</ul></section>
<section><h2>Endpoints</h2><p><code>/healthz</code> · <code>/api/status</code> · controls require <code>WEB_TOKEN</code>.</p></section>
</main></body></html>"""


class HealthServer:
    def __init__(self, config: BotConfig, engine: BotEngine) -> None:
        self.config = config
        self.engine = engine
        handler = type(
            "BoundStatusHandler",
            (StatusHandler,),
            {"engine": engine, "config": config},
        )
        self.httpd = ThreadingHTTPServer((config.web_host, config.web_port), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="health-server",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        logger.info("dashboard listening on http://%s:%s", self.config.web_host, self.config.web_port)
        self.thread.start()
        self._started = True

    def stop(self) -> None:
        if self._started:
            self.httpd.shutdown()
            self._started = False
        self.httpd.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
