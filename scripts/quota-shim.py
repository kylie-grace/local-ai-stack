#!/usr/bin/env python3
"""
Codexbar llmproxy shim for LiteLLM.

Codexbar polls GET /v1/quota-stats with the virtual key in the Authorization
header. LiteLLM exposes this data at /key/info. This server translates between
them so Codexbar can display per-budget spend from the local LiteLLM proxy.

Run: python3 quota-shim.py  (or via launchd com.local-ai.quota-shim)
Port: 4001
"""
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

LITELLM = os.getenv("LITELLM_URL", "http://localhost:4000")
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
PORT = int(os.getenv("SHIM_PORT", "4001"))


def next_month_first(now: datetime) -> str:
    if now.month == 12:
        d = now.replace(year=now.year + 1, month=1, day=1,
                        hour=0, minute=0, second=0, microsecond=0)
    else:
        d = now.replace(month=now.month + 1, day=1,
                        hour=0, minute=0, second=0, microsecond=0)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Log to stdout so launchd captures it in quota-shim.log
        print(f"[quota-shim] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self):
        if self.path != "/v1/quota-stats":
            self._send(404, b"not found")
            return

        auth = self.headers.get("Authorization", "")
        key = auth.removeprefix("Bearer ").strip() or MASTER_KEY

        url = f"{LITELLM}/key/info?" + urllib.parse.urlencode({"key": key})
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {MASTER_KEY}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            print(f"[quota-shim] upstream error: {exc}", file=sys.stderr, flush=True)
            self._send(502, b"upstream error")
            return

        info = data.get("info", {})
        spend = float(info.get("spend") or 0)
        budget = float(info.get("max_budget") or 0)
        alias = info.get("key_alias") or info.get("key_name") or "LiteLLM"
        now = datetime.now(timezone.utc)

        # Fetch aggregate token counts from spend logs
        tokens_in = tokens_out = tokens_total = req_count = 0
        try:
            logs_url = f"{LITELLM}/spend/logs?" + urllib.parse.urlencode(
                {"api_key": key, "limit": 10000}
            )
            logs_req = urllib.request.Request(
                logs_url, headers={"Authorization": f"Bearer {MASTER_KEY}"}
            )
            with urllib.request.urlopen(logs_req, timeout=5) as resp:
                logs_data = json.loads(resp.read())
            logs = logs_data if isinstance(logs_data, list) else logs_data.get("data", []) or []
            req_count = len(logs)
            tokens_in = sum(x.get("prompt_tokens") or 0 for x in logs)
            tokens_out = sum(x.get("completion_tokens") or 0 for x in logs)
            tokens_total = sum(x.get("total_tokens") or 0 for x in logs)
        except Exception as exc:
            print(f"[quota-shim] spend/logs error (non-fatal): {exc}", file=sys.stderr, flush=True)

        reset_at = next_month_first(now)
        cost_nanos = int(spend * 1_000_000_000)
        body = json.dumps({
            # Core quota fields (LLMProxyQuotaStatsResponse top level)
            "planName": alias,
            "usedQuota": round(spend, 6),
            "totalQuota": budget,
            "remainingQuota": round(max(0.0, budget - spend), 6),
            "resetsAt": reset_at,
            "updatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "credits",
            # providers: [String: ProviderStats] — required by Codexbar decoder
            "providers": {
                "litellm": {
                    "tokens": {
                        "totalTokens": tokens_total,
                        "inputTokens": tokens_in,
                        "outputTokens": tokens_out,
                    },
                    "activeCount": 1,
                    "exhaustedCount": 0,
                    "approximateCost": round(spend, 6),
                    "quotaGroups": [],
                }
            },
            # summary: cost history aggregate
            "summary": {
                "totalTokens": tokens_total,
                "tokensIn": tokens_in,
                "tokensOut": tokens_out,
                "costNanos": cost_nanos,
            },
        }).encode()
        self._send(200, body, "application/json")

    def _send(self, code: int, body: bytes, ct: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[quota-shim] listening on http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
