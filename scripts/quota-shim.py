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

        # Schema must match CodexBar's LLMProxyQuotaStatsResponse decoder
        # (Sources/CodexBarCore/Providers/LLMProxy/LLMProxyUsageFetcher.swift).
        # ALL keys are snake_case. CodexBar derives:
        #   - tokens shown  = input_cached + input_uncached + output  (per provider)
        #   - requests      = summary.total_requests (else sum of providers)
        #   - tokens total  = summary.total_tokens   (else sum of providers)
        #   - cost          = summary.approx_cost    (else sum of providers)
        #   - "% used" bar  = derived from quota_groups[].remaining_percent
        # There is no top-level budget field in this version; the budget bar is
        # driven entirely by quota_groups.remaining_percent, so we compute it.
        remaining_percent = (
            round(max(0.0, budget - spend) / budget * 100.0, 4) if budget > 0 else None
        )
        quota_groups = []
        if remaining_percent is not None:
            quota_groups = [{
                "name": alias,
                "remaining_percent": remaining_percent,
                "reset_time": reset_at,
            }]

        body = json.dumps({
            "providers": {
                "litellm": {
                    "credential_count": 1,
                    "active_count": 1,
                    "exhausted_count": 0,
                    "total_requests": req_count,
                    "tokens": {
                        "input_cached": 0,
                        "input_uncached": tokens_in,
                        "output": tokens_out,
                    },
                    "approx_cost": round(spend, 6),
                    "quota_groups": quota_groups,
                }
            },
            "summary": {
                "total_requests": req_count,
                "total_tokens": tokens_total,
                "approx_cost": round(spend, 6),
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
