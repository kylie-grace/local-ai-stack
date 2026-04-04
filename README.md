# my local ai dev stack

A fully local, security-hardened AI proxy stack that gives you a **single OpenAI-compatible endpoint** routing to every model you have access to — enterprise, cloud, and local — from one interface, with a unified browser-based chat UI on top. This is what I use day to day. Sharing in the hopes it might be useful to others. 

## What it does

Instead of configuring every tool (IDE extensions, Claude Code, scripts) with separate API keys and base URLs for each provider, you point them all at `http://localhost:4000`. The stack routes requests to the right backend automatically, with transparent failover between providers.

```
Your tools  →  LiteLLM :4000  →  GitHub Models (free tier, PAT)
                               →  GitHub Copilot Enterprise (OAuth via gh CLI)
                               →  Anthropic Claude (OAuth via CLIProxyAPI)
                               →  Google Gemini (OAuth via CLIProxyAPI)
                               →  LM Studio / Ollama :1234 (local models)

Open WebUI :3000  →  LiteLLM :4000  →  all of the above (browser chat UI)
                →  SearXNG :8080    →  web search (enabled in Open WebUI settings)

HolyClaude :3001  →  LiteLLM :4000  →  all of the above (Claude Code workstation)
                  →  SearXNG :8080  →  web search (via host.docker.internal)
```

**Failover is automatic:** `claude-sonnet` tries Anthropic OAuth (via CLIProxyAPI) first → if rate-limited, falls back to Copilot Enterprise (monthly quota) → then to local LM Studio. You always use the same model name.

> **Why OAuth first?** Anthropic OAuth via CLIProxyAPI gives you fresh daily/hourly limits from the real Anthropic API. Copilot Enterprise has a deeper monthly quota but a lower rate ceiling. Routing OAuth-first maximises throughput during active sessions and uses Copilot as the high-volume overflow bucket.

## Why

- **One key to rule them all** — every tool uses the same `LITELLM_MASTER_KEY` against `localhost:4000`
- **Automatic failover** — session limits and rate limits route around transparently
- **No credentials scattered** — all secrets live in a gitignored `.env`; nothing sensitive is committed
- **Completely local inbound** — all ports are bound to `127.0.0.1`. Nothing on your LAN or VPN can reach this stack
- **Immutable images** — both service images are pinned by digest, not just tag, so nothing changes under you
- **No telemetry, no callbacks** — prompts and responses never leave your machine to any third-party observability service
- **Docker MCP** — if you use Docker Desktop's MCP toolkit, both Claude Code and HolyClaude are pre-configured to use it

> **Why v1.83.0-nightly specifically?** Later versions of LiteLLM introduced a CVE where `success_callback` and `failure_callback` could be used to silently exfiltrate prompts and responses to external services. v1.83.0-nightly predates this and has those callbacks explicitly locked to empty arrays. Do not upgrade without reviewing the LiteLLM release notes.

## Stack

| Service | Image | Port | Purpose |
|---|---|---|---|
| **LiteLLM** | `ghcr.io/berriai/litellm` v1.83.0-nightly | `:4000` | Proxy + admin UI + failover routing |
| **CLIProxyAPI** | `eceasy/cli-proxy-api` v6.9.13 | `:8317` | OAuth bridge for Claude + Gemini |
| **Open WebUI** | `ghcr.io/open-webui/open-webui` v0.6.5 | `:3000` | Browser chat UI |
| **SearXNG** | `searxng/searxng` | `:8080` | Private metasearch engine (Open WebUI + HolyClaude) |
| **PostgreSQL** | `postgres:16-alpine` | internal only | LiteLLM backend DB |
| **HolyClaude** | `coderluii/holyclaude` | `:3001` | Claude Code workstation (separate compose) |

## Prerequisites

- Docker Desktop (with Docker CLI — needed for HolyClaude's Docker MCP)
- GitHub CLI (`gh`) installed and authenticated — needed for Copilot Enterprise token
- LM Studio running locally on port `1234` (optional — for local model routing)
- A GitHub account with a classic PAT for GitHub Models free tier
- A GitHub Copilot Enterprise seat for premium models

## Setup

### 1. Clone and configure

```bash
git clone <this-repo>
cd local-ai
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | How to get it |
|---|---|
| `LITELLM_MASTER_KEY` | `openssl rand -hex 32` — prefix with `sk-local-` |
| `GITHUB_API_KEY` | github.com → Settings → Developer settings → Tokens (classic) → no special scope needed. If your org uses SAML SSO: click "Configure SSO" → Authorize next to the token after creation. |
| `COPILOT_TOKEN` | `gh auth token` (requires `gh auth login` first). See [Copilot Enterprise](#github-copilot-enterprise) below. |
| `POSTGRES_PASSWORD` | `openssl rand -hex 16` |
| `CLIPROXY_MANAGEMENT_KEY` | Any strong string — `openssl rand -hex 20` works. Also set this in `cliproxyapi/config.yaml`. |

### 2. Set your CLIProxyAPI management key

Open `cliproxyapi/config.yaml` and set the `secret-key` field under `remote-management`:

```yaml
remote-management:
  allow-remote: true
  secret-key: "your-key-here"   # same value as CLIPROXY_MANAGEMENT_KEY in .env
  disable-control-panel: false
  panel-github-repository: "https://github.com/router-for-me/Cli-Proxy-API-Management-Center"
```

> **Important — do NOT add `:ro` to the config volume mount.** CLIProxyAPI bcrypt-hashes the `secret-key` on first boot and writes it back to the file. A read-only mount silently breaks management route registration.

Then tell git to stop tracking your local changes to this file:

```bash
git update-index --skip-worktree cliproxyapi/config.yaml
```

### 3. Start the main stack

```bash
docker compose up -d
```

Postgres starts first, LiteLLM waits for it to be healthy, Open WebUI waits for LiteLLM. CLIProxyAPI starts independently.

### 4. Log into providers via CLIProxyAPI

Open **http://localhost:8317/management.html** and enter your `CLIPROXY_MANAGEMENT_KEY`.

> **Troubleshooting management access:** If you get 404 on the management API, check that `MANAGEMENT_PASSWORD` is set in the `cli-proxy-api` service environment in `docker-compose.yml`. This env var is the primary auth path — the config-file bcrypt hash is a fallback. Both are set by default in this repo.
>
> **Model name alignment:** After logging in, verify model names in `litellm-config.yaml` match what CLIProxyAPI serves. Check: `curl http://localhost:8317/v0/management/auth-files/models -H "Authorization: Bearer <your-key>"`
>
> **CLIProxyAPI logs are NOT in Docker stdout.** `docker logs cli-proxy-api` will appear empty. Real request logs are written to the `cliproxyapi-logs` volume at `/CLIProxyAPI/logs/main.log`. To view them: `docker exec cli-proxy-api tail -f /CLIProxyAPI/logs/main.log`

From the management UI:
- **Claude** — click the Claude login button, complete the Anthropic OAuth browser flow
- **Gemini** — click the Gemini login button, complete the Google OAuth browser flow

Credentials are saved to the `cliproxyapi-auths` Docker volume and survive restarts.

### 5. GitHub Copilot Enterprise

Copilot Enterprise uses an OAuth token from the `gh` CLI — not a PAT.

```bash
gh auth login    # one-time, if not already authenticated
gh auth token    # copy this value into .env as COPILOT_TOKEN
```

**Token refresh:** tokens are long-lived but do expire. When Copilot models start returning 401:
```bash
gh auth refresh
gh auth token    # copy new value into .env COPILOT_TOKEN
docker compose restart litellm
```

### 6. Local models (LM Studio or Ollama)

Local model inference is optional. The stack routes `local/*` model names to whatever OpenAI-compatible server you run on port 1234. [Ollama](https://ollama.com) is a great choice — it's simpler to set up, has one-command model installs (`ollama pull llama3`), and runs well on Apple Silicon. [LM Studio](https://lmstudio.ai) is another solid option with a GUI for browsing and loading models.

**LM Studio:**
1. Open LM Studio → Settings → Server → Start server (default port: 1234)
2. Verify: `curl http://localhost:1234/v1/models`

**Ollama:**
1. `brew install ollama && ollama serve`
2. `ollama pull <model>` for each model you want available
3. Ollama serves on port 11434 by default — update `api_base` in `litellm-config.yaml` to `http://host.docker.internal:11434/v1`

Named `local/*` aliases are pre-configured for 9 models. If an alias fails, verify the exact model ID from the models endpoint and update `litellm-config.yaml`.

> **Health dashboard note:** `local/*` models will show **unhealthy** in the LiteLLM dashboard when they aren't loaded. This is expected — load the model in LM Studio (or pull it in Ollama) and the health check turns green automatically. The `local/deepseek-r1-7b` entry will be healthy if that model is loaded, and so on for each one.

### 7. SearXNG (private web search)

SearXNG runs as part of the main stack and is accessible at **http://localhost:8080**.

**Wire into Open WebUI (one-time):**
1. Open **http://localhost:3000** → Admin panel → Settings → Web Search
2. Enable web search → provider: `SearXNG`
3. URL: **`http://searxng:8080/search?q=%s`** ← must use the Docker service name, not `localhost` or `127.0.0.1`. From inside the Open WebUI container those addresses resolve to the container itself, not the host or SearXNG.
4. Save. Web search is now available in Open WebUI conversations.

**HolyClaude access:** From inside HolyClaude, SearXNG is reachable at `http://host.docker.internal:8080`. You can configure this as a web search URL in any tool or MCP server running inside HolyClaude.

**Browser integration (Chrome / Brave):** SearXNG can be set as your default search engine. See [Replacing Google with SearXNG as the default in Chrome](https://blog.ktz.me/replacing-google-with-searxng-as-the-default-in-chrome/). The search URL to use is `http://localhost:8080/search?q=%s`.

> **JSON API:** The `formats: [html, json]` setting in `searxng/settings.yml` enables the JSON endpoint at `/search?q=<query>&format=json`. This is what Open WebUI calls internally. If the JSON endpoint returns 400, verify `formats` includes `json` and restart: `docker compose restart searxng`.

### 8. Open WebUI (browser chat)

Browse to **http://localhost:3000**. On first run, create an admin account — local only, no external registration.

All LiteLLM model routes appear automatically in the model selector.

### 9. HolyClaude (Claude Code workstation)

HolyClaude is a full Claude Code environment with a browser-based IDE. It runs **separately** from the main stack so you can stop it to free memory without affecting LiteLLM/Claude/Gemini.

```bash
# Start HolyClaude
docker compose -f docker-compose.holyclaude.yml up -d

# Stop it (frees ~10GB RAM) without affecting the main stack
docker compose -f docker-compose.holyclaude.yml down
```

Browse to **http://localhost:3001**.

HolyClaude is pre-configured to:
- Route all Claude Code requests through LiteLLM (`ANTHROPIC_BASE_URL=http://host.docker.internal:4000`)
- Access all LiteLLM model routes (Gemini, Copilot, GitHub, local) via `OPENAI_API_BASE_URL=http://host.docker.internal:4000/v1` — use any model by name (e.g. `gemini-flash`, `copilot/claude-sonnet`, `local/qwen3-coder`)
- Use Docker MCP — the same `docker mcp gateway run` MCP server you use on your host is available inside HolyClaude via the mounted Docker socket

Your workspace files are in `holyclaude/workspace/` on the host — accessible from both the HolyClaude UI and directly on your Mac.

**One-time setup inside HolyClaude** (run in the HolyClaude terminal at http://localhost:3001 — all persist in the `holy-claude-home` volume):

```bash
# Task management
npm install -g task-master-ai
```

**Gemini in HolyClaude:** The native Gemini CLI tab is not available for Google Workspace accounts (umich.edu accounts get `RESTRICTED_DASHER_USER` — a Google policy restriction). Use `gemini-flash` or `gemini-pro` via the LiteLLM endpoint instead — these route through CLIProxyAPI and are fully available via `OPENAI_API_BASE_URL`.

**Cursor login:** HolyClaude supports Cursor — add credentials in the HolyClaude settings UI.

**SearXNG in HolyClaude:** Available at `http://host.docker.internal:8080` from within the container.

> **Docker MCP note:** Both your host Claude Code and HolyClaude use Docker Desktop's MCP toolkit (`docker mcp gateway run`). This is configured in Claude Code's `~/.claude.json` on the host, and in `holyclaude/claude.json` which is mounted into the container. If you add new MCP servers to your host config, mirror them in `holyclaude/claude.json`.

### 10. Verify

```bash
# LiteLLM health
curl http://localhost:4000/health/liveliness

# SearXNG health
curl -s "http://localhost:8080/search?q=test&format=json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'SearXNG OK — {len(d[\"results\"])} results')"

# List available models
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer sk-local-YOUR_MASTER_KEY"

# Quick smoke test — failover transparent to caller
curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-local-YOUR_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet","messages":[{"role":"user","content":"say hi"}],"max_tokens":10}'

# Usage/spend logs (token counts per request — spend is $0 for OAuth providers without pricing config)
curl -s "http://localhost:4000/spend/logs?limit=5" \
  -H "Authorization: Bearer sk-local-YOUR_MASTER_KEY" | python3 -m json.tool
```

### 11. Wire up your tools

Anywhere you configure an OpenAI-compatible client:

```
Base URL:  http://localhost:4000/v1
API Key:   <your LITELLM_MASTER_KEY>
```

**Claude Code** — `~/.claude/settings.json` requires **both** a virtual key and the base URL:

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "<your-litellm-virtual-key>",
    "ANTHROPIC_BASE_URL": "http://localhost:4000"
  }
}
```

Generate the virtual key once after the stack is running:

```bash
MASTER_KEY=$(grep "^LITELLM_MASTER_KEY=" ~/dev\ env/local-ai/.env | cut -d= -f2)
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"claude-code","duration":null,"models":[]}'
# Copy the "key" value from the response into settings.json as ANTHROPIC_API_KEY
```

> **Why the virtual key?** Claude Code sends `ANTHROPIC_API_KEY` as an auth token to LiteLLM. Without a valid virtual key in LiteLLM's DB, every request gets a 401. The master key also works here, but a separate virtual key is cleaner.
>
> **If Claude Code gets 401 "Invalid proxy server token"** after a Postgres restart or stack wipe, the virtual key was lost. Re-run the `key/generate` curl above and update `settings.json`. The master key lives in `.env` and is never lost this way.

Also make sure Claude Code is using the API key env var, **not** a logged-in Anthropic account session. In Claude Code, run `/logout` if you have an active Anthropic session — the env var takes precedence once you're logged out.

## Model reference

### Anthropic Claude (CLIProxyAPI OAuth primary — Copilot Enterprise fallback)

| Model name | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|
| `claude-opus` | CLIProxyAPI OAuth (Anthropic direct) | `copilot/claude-opus` (Copilot Enterprise) | `local/devstral-24b` |
| `claude-sonnet` | CLIProxyAPI OAuth (Anthropic direct) | `copilot/claude-sonnet` (Copilot Enterprise) | `local/qwen3-coder` |
| `claude-haiku` | CLIProxyAPI OAuth (Anthropic direct) | `copilot/claude-haiku` (Copilot Enterprise) | `local/llama-3.1-8b` |

The `-oauth` variants (`claude-sonnet-oauth`, etc.) can also be called directly to bypass routing.

### Google Gemini (via CLIProxyAPI OAuth)

| Model name | Routes to |
|---|---|
| `gemini-flash` | CLIProxyAPI → Google (gemini-2.5-flash) |
| `gemini-pro` | CLIProxyAPI → Google (gemini-2.5-pro) |

### GitHub Copilot Enterprise (OAuth via gh CLI)

Only models confirmed accessible via the Copilot REST API are listed. Models that appear in the Copilot UI but return 403 via the API are excluded.

| Model name | Underlying model | Notes |
|---|---|---|
| `copilot/claude-opus` | claude-opus-4.6 | |
| `copilot/claude-sonnet` | claude-sonnet-4.6 | |
| `copilot/claude-haiku` | claude-haiku-4.5 | |
| `copilot/gpt-5` | gpt-5.4 | |
| `copilot/gpt-5.2-codex` | gpt-5.2-codex | Health check shows red (Codex requires max_tokens ≥ 16; works for real requests) |
| `copilot/gpt-5.3-codex` | gpt-5.3-codex | Health check shows red (same reason) |

### GitHub Models (free tier — classic PAT)

| Model name | Routes to |
|---|---|
| `github/gpt-4o` | GitHub Models free tier |
| `github/gpt-4o-mini` | GitHub Models free tier |
| `github/llama-3.1-405b` | GitHub Models free tier |
| `github/llama-3.1-8b` | GitHub Models free tier |

### LM Studio (local — requires LM Studio server running)

| Model name | Local model |
|---|---|
| `local/qwen3-coder` | qwen3-coder-30b (30B, 17GB) |
| `local/devstral-24b` | devstral-small (24B, 14GB) |
| `local/magistral-24b` | magistral-small (24B, 14GB) |
| `local/gemma3-12b` | gemma-3-12b (12B, 8GB) |
| `local/gpt-oss-20b` | gpt-oss-20b (20B, 12GB) |
| `local/qwen3-vl` | qwen3-vl-4b (4B, 3GB) |
| `local/mistral-nemo` | mistral-nemo-instruct-2407 (7GB) |
| `local/llama-3.1-8b` | meta-llama-3.1-8b-instruct (4.5GB) |
| `local/deepseek-r1-7b` | deepseek-r1-distill-qwen-7b (4.7GB) |
| `lm-studio/<model-id>` | Any LM Studio model, JIT loaded |

## Managing the stack

```bash
# Start main stack (includes SearXNG)
docker compose up -d

# Stop main stack (data preserved in Docker volumes)
docker compose down

# Start HolyClaude (separate — can be stopped independently)
docker compose -f docker-compose.holyclaude.yml up -d
docker compose -f docker-compose.holyclaude.yml down

# View logs
docker compose logs -f litellm
docker compose logs -f open-webui
docker logs -f holy-claude
# CLIProxyAPI does NOT log to Docker stdout — use this instead:
docker exec cli-proxy-api tail -f /CLIProxyAPI/logs/main.log

# Restart a single service
docker compose restart litellm

# IMPORTANT: config file changes need restart, not just up -d
# Editing litellm-config.yaml while the container is running does NOT hot-reload it.
# Always use:
docker compose restart litellm
# NOT: docker compose up -d  (this won't recreate an already-running container)

# Full reset — WARNING: destroys all data including OAuth credentials and chat history
docker compose down -v
```

## LiteLLM v1.83 quirks (read before debugging routing issues)

These are non-obvious behaviors in the pinned version that were discovered the hard way. Do not attempt to "simplify" the config without understanding these first.

### 1. Responses API routing breaks Claude tool calls

**What happens:** LiteLLM v1.83 routes `/v1/messages` requests (what Claude Code sends) through an experimental "Responses API adapter" when the backend provider is `openai`. GitHub Copilot's Responses API endpoint exists but doesn't support Claude models — it returns `unsupported_api_for_model`. Simple text messages work; anything involving tool calls (`tool_use`/`tool_result`) fails silently.

**The fix:** `LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES=true` is set in `docker-compose.yml` for the litellm service. This forces all `/v1/messages` traffic through chat completions instead of the Responses API.

**Symptom if missing:** Claude Code gets one response, then hangs or returns empty tool inputs on all subsequent turns.

### 2. CLIProxyAPI streaming drops tool parameters without `custom_llm_provider`

**What happens:** CLIProxyAPI's OpenAI-compat endpoint (`/v1/chat/completions`) sends tool call arguments in a single streaming chunk rather than incrementally. LiteLLM's streaming iterator only handles incremental chunks — it drops the single-chunk arguments, resulting in tool calls that always have `input: {}`.

**The root cause (two-part):** 
- The LiteLLM router passes the *alias* model name (e.g. `claude-sonnet`) to `litellm.anthropic_messages`, not the deployment model name (`anthropic/claude-sonnet-4-6`). Without a provider prefix, LiteLLM can't determine the provider.
- Without a detected provider, `anthropic_messages_provider_config = None`, so the request falls through to the completion-transformer path that hits `/v1/chat/completions` instead of `/v1/messages`.
- CLIProxyAPI's `/v1/messages` (native Anthropic format) streams arguments correctly with proper `input_json_delta` events. The `/v1/chat/completions` path does not.

**The fix:** All CLIProxyAPI-backed models in `litellm-config.yaml` include:
```yaml
custom_llm_provider: anthropic
api_base: http://cli-proxy-api:8317   # NO /v1 suffix — the anthropic provider appends /v1/messages
```
This ensures the provider is detected correctly even when the router passes the alias name, routing to `/v1/messages` directly.

**Symptom if `custom_llm_provider` is missing:** Streaming tool calls return `"input": {}` — the model seems to call tools but with no arguments. Non-streaming calls work fine (different code path).

**Symptom if `/v1` is in the api_base:** Requests go to `http://cli-proxy-api:8317/v1/v1/messages` (doubled path) and return 404.

### 3. Config changes need `restart`, not `up -d`

**What happens:** After editing `litellm-config.yaml`, running `docker compose up -d` does nothing — the container is already running and the image/env hasn't changed, so Docker skips recreation. The old config stays in memory.

**The fix:** Always use `docker compose restart litellm` after editing `litellm-config.yaml`.

**Symptom if missed:** Changes appear to have no effect; you spend time debugging config that was never loaded.

### 4. Virtual keys are lost on Postgres wipe

LiteLLM stores virtual keys (the `sk-...` tokens callers use) in the Postgres `LiteLLM_VerificationTokenTable`. If Postgres is wiped or the volume is removed, all virtual keys disappear.

**Symptom:**
```
401 {"error":{"message":"Authentication Error, Invalid proxy server token passed...
Unable to find token in cache or LiteLLM_VerificationTokenTable"}}
```

**Fix — regenerate the virtual key:**
```bash
MASTER_KEY=$(grep "^LITELLM_MASTER_KEY=" ~/dev\ env/local-ai/.env | cut -d= -f2)
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"claude-code","duration":null,"models":[]}'
```

Update `ANTHROPIC_API_KEY` in `~/.claude/settings.json` with the returned `"key"` value.

> **The master key never lives in the DB** — `LITELLM_MASTER_KEY` from `.env` always works and survives a Postgres wipe. Only virtual keys are lost.

### 5. Docker VM disk capacity

LiteLLM (and the stack generally) fails hard if Docker's internal VM disk runs out of space. Postgres panics with `No space left on device` and enters a crash-recovery loop, taking the whole stack down.

**Check disk usage:**
```bash
docker system df
```

**If >80% full, prune before heavy workloads:**
```bash
docker image prune -f    # dangling images
docker builder prune -f  # build cache
# docker volume prune -f  # ⚠️ check first — will destroy data if not careful
```

**Automated prevention:** A macOS `launchd` job (`com.local-ai.docker-prune`) runs `docker image prune -f` and `docker container prune -f` daily at 3am. Log at `/tmp/docker-prune.log`. The plist is at `~/Library/LaunchAgents/com.local-ai.docker-prune.plist` — not in this repo (host-specific).

> See `copilot-repairs.md` for the full incident log from 2026-04-04 when this took the stack down.

## Security notes

### Inbound network
- All published ports are bound to `127.0.0.1` — nothing on your LAN or VPN can connect
- Postgres (`5432`) has no host port at all — internal-only to the Docker bridge
- `docker ps` should show all entries as `127.0.0.1:PORT->PORT/tcp`, never `0.0.0.0:PORT->PORT/tcp`

### Docker networks
- All main stack containers share `litellm-net` (isolated bridge) — they can reach each other by service name but are not reachable from outside Docker
- HolyClaude runs on its own default network (`local-ai_default`) and reaches the main stack via `host.docker.internal` (Mac Docker Desktop host-gateway)
- SearXNG is on `litellm-net` — Open WebUI and LiteLLM can reach it at `http://searxng:8080`; host can reach it at `http://localhost:8080`

### Container hardening
- `no-new-privileges: true` and `cap_drop: ALL` on all main stack containers
- HolyClaude mounts `/var/run/docker.sock` for Docker MCP — grants full Docker control from within the container. Acceptable since it's localhost-only and operator-controlled, but do not expose HolyClaude's port externally

### Images and secrets
- All images pinned by SHA256 digest — tags cannot be silently re-pointed to a different image
- `.env` is gitignored; `cliproxyapi/config.yaml` is marked `skip-worktree` after you set your management key

### Telemetry and callbacks
- `success_callback` and `failure_callback` are explicitly empty in `litellm-config.yaml` — prompts and responses are never forwarded to external services
- `LITELLM_TELEMETRY: "False"` and `DISABLE_TELEMETRY_REPORTING: "True"` set in the container environment
- LiteLLM locked to v1.83.0-nightly to avoid a later CVE where callbacks could be used to silently exfiltrate prompts

## Updating

Images are pinned by digest intentionally. To update:

1. Find the new digest: `docker buildx imagetools inspect <image>:<tag>`
2. Update the `image:` line in the relevant compose file
3. `docker compose pull && docker compose up -d`

For LiteLLM: check [release notes](https://github.com/BerriAI/litellm/releases) for CVEs before upgrading.

## Related tools

- **[HolyClaude](https://github.com/CoderLuii/HolyClaude)** — enhanced Claude Code workstation, runs as a companion container alongside this stack. Pre-configured to route through LiteLLM and use Docker MCP. See `docker-compose.holyclaude.yml`.
- **[Docker MCP Toolkit](https://docs.docker.com/ai/mcp-catalog-and-toolkit/)** — Docker Desktop's MCP server catalog. Both host Claude Code and HolyClaude use this.

## Usage tracking

LiteLLM logs every request to Postgres with token counts (prompt, completion, cache read/write) per model. The admin UI at `http://localhost:4000/ui` shows this under the Usage tab.

**Token counts are always populated.** Dollar spend shows `$0.00` for OAuth-backed models (CLIProxyAPI, Copilot) because there's no static pricing to map against. If you want dollar estimates, add `input_cost_per_token` / `output_cost_per_token` to the relevant model entries in `litellm-config.yaml`.

### OpenUsage compatibility

[openusage](https://github.com/robinebers/openusage) is a macOS menu bar app showing usage across AI providers. Getting it to display Anthropic usage with this proxy stack required a fork — here's the full story.

**The conflict:** The upstream Claude plugin reads `~/.claude/.credentials.json` for the Anthropic OAuth token. Claude Code CLI *also* reads that exact file on startup. If the file exists, Claude Code uses the OAuth session directly and ignores `ANTHROPIC_BASE_URL` — bypassing LiteLLM entirely. This is why `/logout` is required when using the proxy. You cannot keep credentials in that file permanently.

**The fix:** A [patched fork](https://github.com/kylie-grace/openusage) changes the credential path to `~/.local-ai/anthropic-token.json` — a path Claude Code never reads. The macOS keychain fallback is also disabled (same conflict risk). A `launchd` agent keeps the file fresh automatically.

**What the patch changes in `plugins/claude/plugin.js`:**
```js
// Upstream (conflicts with proxy):
const CRED_FILE = "~/.claude/.credentials.json"

// Patched fork (safe — Claude Code never reads this path):
const CRED_FILE = "~/.local-ai/anthropic-token.json"
```
That is the only functional change. The token format, refresh logic, and usage API calls are identical to upstream.

**Token format written by `scripts/sync-anthropic-token.sh`:**
```json
{"claudeAiOauth":{"accessToken":"sk-ant-oat01-...","refreshToken":"sk-ant-ort01-...","expiresAt":1234567890000}}
```
CLIProxyAPI stores tokens differently (`access_token`, `refresh_token`, `expired` ISO8601). The sync script converts between the two formats.

**Setup (one-time):**

```bash
# 1. Build the patched fork (bun + Rust required — install once)
curl -fsSL https://bun.sh/install | bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source ~/.cargo/env

git clone https://github.com/kylie-grace/openusage.git ~/dev\ env/openusage
cd ~/dev\ env/openusage
bun install && bun run bundle:plugins && bun run tauri build

# 2. Install
cp -r src-tauri/target/release/bundle/macos/OpenUsage.app /Applications/

# 3. Load the launchd sync agent (already in ~/Library/LaunchAgents/ from stack setup)
launchctl load ~/Library/LaunchAgents/com.local-ai.sync-anthropic-token.plist

# 4. Open the app
open /Applications/OpenUsage.app
```

**Ongoing maintenance:**
- Token syncs automatically every 15 min. Log: `/tmp/sync-anthropic-token.log`
- Manual sync: `~/dev\ env/local-ai/scripts/sync-anthropic-token.sh`
- If the launchd agent isn't loaded after a reboot: `launchctl load ~/Library/LaunchAgents/com.local-ai.sync-anthropic-token.plist`
- To rebuild after changes to the fork: `cd ~/dev\ env/openusage && bun run bundle:plugins && bun run tauri build`

> **Why not just use LiteLLM's usage UI?** You can — `http://localhost:4000/ui` → Usage tab shows token counts per model. But openusage also shows Copilot, Cursor, and Codex usage in one panel, which LiteLLM doesn't have visibility into.

## Future goals

- **OpenUsage custom plugin** — ✅ Done. Patched fork at https://github.com/kylie-grace/openusage reads `~/.local-ai/anthropic-token.json`. Synced every 15min by `com.local-ai.sync-anthropic-token` launchd agent from CLIProxyAPI Docker container. Build with `bun run tauri build` in the fork.
- **LiteLLM MCP support** — LiteLLM has a [built-in MCP server](https://docs.litellm.ai/docs/mcp) that exposes all your model routes as MCP tools. The intent is to wire this in so any MCP-aware client (Claude Code, Open WebUI, HolyClaude) can discover and call models via the MCP protocol rather than hand-configuring base URLs.
- **Open WebUI MCP** — Open WebUI supports MCP via HTTP/SSE. Docker MCP (`docker mcp gateway run`) is stdio-based; bridging the two requires a sidecar like `supergateway`. Tracked for a future session.
- **Copilot token auto-refresh** — automate `gh auth refresh && gh auth token` → restart litellm on a schedule so tokens never expire silently.
- **LiteLLM model pricing** — add `input_cost_per_token` / `output_cost_per_token` to model entries in `litellm-config.yaml` so the Usage tab shows dollar estimates alongside token counts.
