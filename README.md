# local-ai

A privacy-first AI proxy stack for University of Michigan researchers and staff. Routes all model requests through a single local endpoint, unified across UM GPT Toolkit cloud models and local LM Studio inference — with a full browser chat UI, MCP tools, spend tracking, and content guardrails.

## What it does

```
   callers                                       providers
   ──────────────────────────────────────────────────────────────────────
                        ┌────────────────────┐
   Claude Code ─────────►                    ├── UM GPT Toolkit ─► Anthropic, OpenAI,
   Open WebUI ──────────►   LiteLLM :4000    │   (api.toolkit.umgpt.umich.edu/v1)
   IDE / scripts ────────►                   │     Claude, GPT-5, o3, Gemini, Llama, images
                         │   unified OpenAI  │
                         │   proxy + routing ├── LM Studio :1234 ─► local models
                         │   + spend logs    │     qwen3-coder, devstral, gemma, nomic-embed
                         │   + guardrails    │
                         └────────┬──────────┘
                               PostgreSQL :5432

   Open WebUI chat ──────────────────────────── SearXNG :8080 ─── web search

   Open WebUI tools ─────── mcp-proxy ─────── Docker MCP Gateway :8811
                                                 AWS CDK, Context7, DuckDuckGo,
                                                 Playwright, Gmail, and more

   Codexbar ────────── quota-shim :4001 ─────── LiteLLM /key/info
                        (launchd service)         tracks budget per virtual key
```

**You point every tool at `http://localhost:4000`.** Model routing, failover, spend tracking, and guardrails happen automatically.

## Stack

| Service | Image | Port | Purpose |
|---|---|---|---|
| **LiteLLM** | `ghcr.io/berriai/litellm:main-latest` | `:4000` | Proxy + admin UI + routing |
| **Open WebUI** | `ghcr.io/open-webui/open-webui:latest` | `:3000` | Browser chat UI (v0.9.6) |
| **SearXNG** | `searxng/searxng:latest` | `:8080` | Private metasearch engine |
| **mcp-proxy** | `nginxinc/nginx-unprivileged:alpine` | internal | Host-header rewrite for Docker MCP |
| **PostgreSQL** | `postgres:16-alpine` | internal only | LiteLLM backend DB |
| **[launchd] Docker MCP Gateway** | `docker mcp gateway` | `:8811` | MCP tools (Playwright, AWS, etc.) |
| **[launchd] quota-shim** | Python HTTP server | `:4001` | Codexbar spend tracking bridge |

All published ports are bound to `127.0.0.1` — nothing reachable from outside your machine.

Images are pinned by SHA256 digest in `docker-compose.yml` so nothing changes under you.

## Prerequisites

- **macOS** with Docker Desktop installed (MCP toolkit requires Docker Desktop)
- **UM GPT Toolkit API key** — request at https://its.umich.edu/computing/ai/gpt-toolkit-in-depth
- **LM Studio** (optional) — for local model inference on port 1234
- Python 3 installed (`/usr/bin/python3` — ships with macOS)

## Setup

### 1. Clone and configure

```bash
git clone <this-repo>
cd local-ai
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Value |
|---|---|
| `LITELLM_MASTER_KEY` | `openssl rand -hex 32` — prefix with `sk-local-` |
| `POSTGRES_PASSWORD` | `openssl rand -hex 16` |
| `UMGPT_API_KEY` | Your UM GPT Toolkit general API key |
| `UMGPT_API_URL` | `https://api.toolkit.umgpt.umich.edu/v1` |
| `UMGPT_CLAUDE_CODE_KEY` | A second UM GPT key for Claude Code spend tracking (can be same key) |
| `UMGPT_CLAUDE_CODE_URL` | `https://api.toolkit.umgpt.umich.edu` |

### 2. Start the Docker stack

```bash
docker compose up -d
```

Postgres starts first, LiteLLM waits for it to be healthy, Open WebUI waits for LiteLLM. All services have restart policies.

### 3. Create LiteLLM virtual keys

Virtual keys let you set per-key monthly spend budgets and attribute usage to different workloads. Create two:

```bash
MASTER_KEY=$(grep "^LITELLM_MASTER_KEY=" .env | cut -d= -f2)

# General use key — $250/mo budget
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"umgpt-monthly","duration":null,"max_budget":250,"budget_duration":"monthly","models":[]}'

# Claude Code key — $100/mo budget (used when routing Claude Code through LiteLLM)
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"claude-code-tracking","duration":null,"max_budget":100,"budget_duration":"monthly","models":[]}'
```

Copy the returned `"key"` values — you'll need them for Codexbar and Phase 13.

### 4. Install the host-side launchd services

Two lightweight services run outside Docker and start automatically at login:

#### Docker MCP Gateway

Exposes Docker Desktop's MCP toolkit (Playwright, AWS CDK, Context7, DuckDuckGo, etc.) as a single MCP endpoint for Open WebUI and Claude Code.

```bash
# Install the plist (already in this repo for reference — path must match your setup)
cp com.local-ai.mcp-gateway.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.local-ai.mcp-gateway.plist

# Verify it's running
launchctl list com.local-ai.mcp-gateway
tail /tmp/mcp-gateway.log
```

**The plist sets `MCP_GATEWAY_AUTH_TOKEN` to a fixed value.** This token must match the one configured in Open WebUI (Admin → Settings → Tools). The `mcp-proxy` Docker service handles the DNS rebinding restriction by rewriting `Host: host.docker.internal` → `Host: localhost:8811`.

#### Quota Shim

Translates Codexbar's `GET /v1/quota-stats` requests into LiteLLM's `/key/info` (+ `/spend/logs`) endpoints, giving Codexbar real-time spend, request, and token counts from your virtual keys.

> **Schema compatibility (important):** CodexBar's `llmproxy` provider decodes a
> specific JSON shape, defined in
> [`Sources/CodexBarCore/Providers/LLMProxy/LLMProxyUsageFetcher.swift`](https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Providers/LLMProxy/LLMProxyUsageFetcher.swift).
> As of **CodexBar 0.34.0** all keys are **snake_case** and there is no top-level budget
> field — the "% used" bar is derived from `providers.<name>.quota_groups[].remaining_percent`.
> `scripts/quota-shim.py` emits exactly this shape (`total_requests`, `tokens.{input_cached,
> input_uncached,output}`, `approx_cost`, `quota_groups`, and a `summary`). If a CodexBar
> update makes the tabs read zero again, re-check that Swift file — the field names likely changed.

```bash
cp com.local-ai.quota-shim.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.local-ai.quota-shim.plist

# Verify it's running
launchctl list com.local-ai.quota-shim
tail /tmp/quota-shim.log

# Reload after editing the script
launchctl kickstart -k gui/$(id -u)/com.local-ai.quota-shim

# Test a virtual key (replace with your actual key)
curl -s -H "Authorization: Bearer <your-virtual-key>" http://localhost:4001/v1/quota-stats
```

### 5. Set up Codexbar (optional — spend tracking menu bar app)

[Codexbar](https://codexbar.app) is a macOS menu bar app that shows AI spend. Once the quota shim is running:

1. Quit Codexbar if running
2. In Codexbar → Settings → LLM Proxy:
   - **Enterprise Host**: `http://localhost:4001/v1`
3. Add each virtual key as a separate "LLM Proxy" account with the matching `sk-...` key value
4. Codexbar polls `/v1/quota-stats` every few minutes — you'll see `$X.XX / $250.00` etc.

### 6. Configure Open WebUI (one-time admin setup)

Browse to **http://localhost:3000** — create an admin account on first run.

These are already configured in the database by default (see `scripts/update_webui_config.py` for the programmatic setup):
- **Web search**: SearXNG at `http://searxng:8080` — enabled by default
- **Embedding**: `local/nomic-embed-v1.5` via LiteLLM (chunk 1500/100, top-k 5)
- **Image generation**: `umgpt/gpt-image-2` via LiteLLM
- **Default model**: `umgpt/claude-sonnet-4-6`
- **MCP tools**: Docker MCP Gateway at `http://mcp-proxy:8080/mcp` (MCP type, bearer auth)

Remaining manual steps (Admin → Settings):
- **Personalization → Memory** → Enable

### 7. Wire up Claude Code (optional — Phase 13)

Routes Claude Code sessions through LiteLLM so spend shows up in the usage dashboard and counts against the `claude-code-tracking` budget key.

Add to `~/.claude/settings.json`:
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:4000",
    "ANTHROPIC_API_KEY": "<your claude-code-tracking virtual key>"
  }
}
```

Also requires adding `LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES: "true"` to the litellm service in `docker-compose.yml` — see ROADMAP.md Phase 13.

### 8. Local models (LM Studio — optional)

1. Open LM Studio → Settings → Server → Start server (default port 1234)
2. Load a model in LM Studio
3. Verify: `curl http://localhost:1234/v1/models`

All `local/*` model names in `litellm-config.yaml` route to LM Studio. When LM Studio is not running, these models return errors — the `umgpt/*` models are always available.

### 9. Verify the stack

```bash
# All containers running?
docker compose ps

# LiteLLM healthy?
curl http://localhost:4000/health/liveliness

# List models (umgpt/* always available, local/* only if LM Studio is running)
curl -H "Authorization: Bearer <your-master-key>" http://localhost:4000/v1/models | python3 -m json.tool

# Quick test
curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer <your-master-key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"umgpt/claude-sonnet-4-6","messages":[{"role":"user","content":"say hi"}],"max_tokens":10}'

# SearXNG
curl -s "http://localhost:8080/search?q=test&format=json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK — {len(d[\"results\"])} results')"

# Check spend dashboard
open http://localhost:4000/ui
```

## MCP Tools

The Docker MCP Gateway exposes tools from Docker Desktop's catalog to both Claude Code and Open WebUI.

**Installed tool servers** (from `~/.docker/mcp/registry.yaml`):

| Server | Purpose |
|---|---|
| `aws-cdk-mcp-server` | AWS CDK patterns, CDK Nag compliance |
| `aws-core-mcp-server` | AWS core utilities |
| `aws-documentation` | AWS docs search |
| `cloudflare-docs` | Cloudflare Workers / Pages docs |
| `context7` | Up-to-date library documentation for LLMs |
| `duckduckgo` | Private web search |
| `gmail-mcp` | Gmail read/send (requires Google auth setup) |
| `playwright` | Browser automation |

**Adding more servers:**
```bash
# List available servers
docker mcp catalog ls

# Add a server
echo "  <server-name>:\n    ref: \"\"" >> ~/.docker/mcp/registry.yaml

# Reload the gateway
launchctl unload ~/Library/LaunchAgents/com.local-ai.mcp-gateway.plist
launchctl load ~/Library/LaunchAgents/com.local-ai.mcp-gateway.plist
```

**For Claude Code:** The Docker MCP gateway is configured via `~/.claude.json` under `mcpServers.MCP_DOCKER`. It runs in stdio mode per session.

**For Open WebUI:** Connected as a native MCP tool server via `mcp-proxy:8080/mcp`. Tools appear automatically in chat.

## Model Reference

### UM GPT Toolkit (`umgpt/*`)

Always available via `api.toolkit.umgpt.umich.edu`. UM account charges apply — see [UM GPT Toolkit pricing](https://its.umich.edu/computing/ai/pricing#toolkit-costs).

| Model name | Underlying model | Notes |
|---|---|---|
| `umgpt/claude-sonnet-4-6` | Claude Sonnet 4.6 | Default model |
| `umgpt/claude-opus-4-7` | Claude Opus 4.7 | |
| `umgpt/claude-opus-4-6` | Claude Opus 4.6 | |
| `umgpt/claude-haiku-4-5` | Claude Haiku 4.5 | |
| `umgpt/gpt-5` | GPT-5 | |
| `umgpt/gpt-5-mini` | GPT-5 mini | |
| `umgpt/gpt-5.1` through `gpt-5.5` | GPT-5 variants | |
| `umgpt/gpt-4o` | GPT-4o | |
| `umgpt/gpt-4o-mini` | GPT-4o mini | |
| `umgpt/gpt-4.1` / `gpt-4.1-mini` / `gpt-4.1-nano` | GPT-4.1 family | |
| `umgpt/o1` / `o3` / `o3-mini` / `o4-mini` | Reasoning models | |
| `umgpt/gemini-3-flash-preview` | Gemini 3 Flash | |
| `umgpt/gemini-3.1-flash-image-preview` | Gemini 3.1 Flash | |
| `umgpt/llama-4-maverick` | Llama 4 Maverick | |
| `umgpt/llama-4-scout` | Llama 4 Scout | |
| `umgpt/gpt-image-2` | GPT Image 2 | Image generation |
| `umgpt/gpt-image-1.5` | GPT Image 1.5 | Image generation |
| `umgpt/text-embedding-3-large` | text-embedding-3-large | Embeddings |
| `umgpt/text-embedding-3-small` | text-embedding-3-small | Embeddings |

### LM Studio (`local/*`)

Requires LM Studio running on port 1234. Models show as unhealthy in the LiteLLM dashboard when not loaded — expected.

| Model name | Local model | RAM |
|---|---|---|
| `local/qwen3-coder` | qwen3-coder-30b | ~17GB |
| `local/qwen3.6-35b` | qwen3.6-35b | ~20GB |
| `local/devstral-24b` | devstral-small-2505 | ~14GB |
| `local/qwen3-vl` | qwen3-vl-4b | ~3GB |
| `local/magistral-24b` | magistral-small-2506 | ~14GB |
| `local/gemma3-12b` | gemma-3-12b | ~8GB |
| `local/gemma4-31b` | gemma-4-31b | ~18GB |
| `local/gpt-oss-20b` | gpt-oss-20b | ~12GB |
| `local/mistral-nemo` | mistral-nemo-instruct-2407 | ~7GB |
| `local/llama-3.1-8b` | meta-llama-3.1-8b-instruct | ~4.5GB |
| `local/deepseek-r1-7b` | deepseek-r1-distill-qwen-7b | ~4.7GB |
| `local/nomic-embed-v1.5` | nomic-embed-text-v1.5 | — |
| `local/nomic-embed-v2` | nomic-embed-text-v2 | — |

`local/nomic-embed-v1.5` is the default embedding model for Open WebUI RAG — used whenever you upload documents for retrieval. LM Studio must be running for embeddings to work; falls back to `umgpt/text-embedding-3-small` if configured.

## Guardrails

LiteLLM is configured with a `hide-secrets` guardrail (post-call, always on). This scans model responses for leaked secrets (API keys, tokens, credentials) before returning them to the caller.

To view or modify guardrails, edit `litellm-config.yaml` under the `guardrails:` section and restart LiteLLM:

```bash
docker compose restart litellm
```

Current config:
```yaml
guardrails:
  - guardrail_name: "no-secrets-in-responses"
    litellm_params:
      guardrail: hide-secrets
      default_on: true
      mode: "post_call"
```

## Spend Tracking

LiteLLM logs every request to Postgres with model, tokens (prompt/completion/cache), latency, and cost. View at **http://localhost:4000/ui** → Usage tab.

**Virtual keys** let you set monthly budgets:
- `umgpt-monthly` — $250/mo soft limit (general use)
- `claude-code-tracking` — $100/mo soft limit (Claude Code sessions, Phase 13)

When a key's budget is exceeded, LiteLLM returns `429 Budget Exceeded`. The master key has no budget limit.

**Codexbar** reads spend data from the quota shim at `http://localhost:4001/v1/quota-stats` — it shows real-time `$used / $total` per key in the menu bar.

## Managing the Stack

```bash
# Start everything
docker compose up -d

# Stop (data preserved in Docker volumes)
docker compose down

# Restart a service after config changes
docker compose restart litellm     # after editing litellm-config.yaml
docker compose restart open-webui  # after changing env vars (must be: docker compose up -d open-webui)

# IMPORTANT: env var changes to docker-compose.yml require full recreation, not just restart:
docker compose up -d <service>     # recreates with new env — use this for env changes
docker compose restart <service>   # in-place restart — does NOT pick up env changes

# View logs
docker compose logs -f litellm
docker compose logs -f open-webui
docker compose logs -f mcp-proxy

# Reload MCP gateway (after editing registry.yaml or plist)
launchctl unload ~/Library/LaunchAgents/com.local-ai.mcp-gateway.plist
launchctl load ~/Library/LaunchAgents/com.local-ai.mcp-gateway.plist
tail /tmp/mcp-gateway.log

# Reload quota shim
launchctl unload ~/Library/LaunchAgents/com.local-ai.quota-shim.plist
launchctl load ~/Library/LaunchAgents/com.local-ai.quota-shim.plist
tail /tmp/quota-shim.log

# Full reset — WARNING: destroys all data including chat history and virtual keys
docker compose down -v
```

## Troubleshooting

### LiteLLM 401 "Invalid proxy server token"

The virtual key was not found. Either the key was created against a Postgres instance that was later wiped, or the key value is wrong. Re-create:

```bash
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer <master-key>" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"umgpt-monthly","duration":null,"max_budget":250,"budget_duration":"monthly"}'
```

The master key from `.env` always works and is never lost.

### Open WebUI "Failed to connect to MCP server"

1. Check gateway is running: `launchctl list com.local-ai.mcp-gateway` — look for `"PID" = <number>`
2. Check auth token in plist matches Open WebUI's tool server config — both must use the same `MCP_GATEWAY_AUTH_TOKEN` value
3. Check mcp-proxy is running: `docker compose ps mcp-proxy` — should be `Up`
4. Test manually: `curl -s -H "Authorization: Bearer <token>" http://localhost:8811/mcp` — should not return 401/403

### Codexbar shows "parse error" or "404"

- Ensure the shim is running: `launchctl list com.local-ai.quota-shim`
- Codexbar Enterprise Host must be `http://localhost:4001/v1` (note: ends in `/v1`)
- The key entered in Codexbar must exactly match a LiteLLM virtual key (starts with `sk-`)
- Check shim logs: `tail /tmp/quota-shim.log` and `tail /tmp/quota-shim-error.log`

### LM Studio models return errors

Load the model in LM Studio first. The `local/*` entries in LiteLLM config require the model to be actively loaded and the LM Studio server started on port 1234.

### Docker VM disk space

```bash
docker system df
docker image prune -f
docker builder prune -f
```

## Security Notes

- All published ports bound to `127.0.0.1` — no LAN/VPN access
- Postgres has no host port — internal Docker network only
- All containers use `cap_drop: ALL` and `no-new-privileges: true` (mcp-proxy adds `NET_BIND_SERVICE`)
- Images pinned by SHA256 digest in `docker-compose.yml` — tags cannot be silently re-pointed
- `.env` is gitignored — secrets never committed
- `LITELLM_TELEMETRY: "False"` and `DISABLE_TELEMETRY_REPORTING: "True"` — no usage data sent to LiteLLM
- `success_callback` and `failure_callback` explicitly empty — prompts never forwarded to external services
- The `hide-secrets` guardrail scans all responses for leaked credentials

## Google Drive MCP (Backlog)

Not yet set up. Requires:
1. Google Cloud Console → enable Drive API → create OAuth 2.0 Desktop app credentials
2. Note your `CLIENT_ID` and `CLIENT_SECRET`
3. Configure the Docker MCP server (not yet in Docker's catalog — would run as a custom server)

See ROADMAP.md for status.

## Pending (Phase 13 — Claude Code Routing)

Routes Claude Code → LiteLLM → UM GPT Toolkit for full spend tracking. Do at end of a working session (requires a restart):

1. Add Claude model aliases to `litellm-config.yaml` (see ROADMAP.md Phase 13)
2. Add to `docker-compose.yml` litellm service environment:
   ```yaml
   LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES: "true"
   ```
3. `docker compose up -d litellm`
4. Add to `~/.claude/settings.json`:
   ```json
   { "env": { "ANTHROPIC_BASE_URL": "http://localhost:4000", "ANTHROPIC_API_KEY": "<claude-code-tracking key>" } }
   ```
5. Test: start a Claude Code session, then check http://localhost:4000/ui → Usage
6. Rollback: remove both env vars from `~/.claude/settings.json`

## Service URLs

| Service | URL | Auth |
|---|---|---|
| LiteLLM API | http://localhost:4000/v1 | `Authorization: Bearer <master-key>` |
| LiteLLM admin UI | http://localhost:4000/ui | master key |
| Open WebUI | http://localhost:3000 | local account |
| SearXNG | http://localhost:8080 | none |
| Codexbar quota shim | http://localhost:4001/v1/quota-stats | `Authorization: Bearer <virtual-key>` |
| Docker MCP Gateway (host) | http://localhost:8811/mcp | `Authorization: Bearer <MCP_GATEWAY_AUTH_TOKEN>` |
