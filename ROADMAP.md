# local-ai Roadmap

Track what's done, what's in progress, and what's coming next.

---

## ✅ Completed

### Core stack
- [x] LiteLLM proxy at `localhost:4000` with PostgreSQL backend
- [x] CLIProxyAPI OAuth bridge at `localhost:8317`
- [x] Open WebUI browser chat at `localhost:3000`
- [x] All ports bound to `127.0.0.1` — LAN/VPN cannot reach the stack
- [x] Images pinned by SHA256 digest — immutable
- [x] `cap_drop: ALL` + `no-new-privileges:true` on all main stack containers
- [x] Telemetry disabled everywhere; callbacks empty (no exfiltration)
- [x] Version pinned to v1.83.0-nightly to avoid LiteLLM exfiltration CVE

### Provider integrations
- [x] Claude OAuth session (CLIProxyAPI) — `claude-sonnet`, `claude-opus`, `claude-haiku`
- [x] Gemini OAuth session (CLIProxyAPI) — `gemini-flash`, `gemini-pro`
- [x] GitHub Models free tier — `github/gpt-4o`, `github/llama-3.1-405b`, etc.
- [x] GitHub Copilot Enterprise — full model set: Claude 4.5/4.6, GPT-5.x, codex variants
- [x] LM Studio wildcard routing (`lm-studio/*`) + named `local/*` aliases for all 9 library models

### Routing
- [x] Automatic failover: `claude-sonnet` → `copilot/claude-sonnet` → `local/qwen3-coder`
- [x] Automatic failover: `claude-opus` → `copilot/claude-opus` → `local/devstral-24b`
- [x] Automatic failover: `claude-haiku` → `copilot/claude-haiku` → `local/llama-3.1-8b`

### Management
- [x] CLIProxyAPI management UI at `http://localhost:8317/management.html`
- [x] `MANAGEMENT_PASSWORD` env var as primary auth path for management routes
- [x] `git update-index --skip-worktree` for CLIProxyAPI config

### HolyClaude
- [x] Separate `docker-compose.holyclaude.yml` — starts/stops independently
- [x] Container name `holy-claude`, web UI at `localhost:3001`
- [x] Routed through LiteLLM via `ANTHROPIC_BASE_URL=http://host.docker.internal:4000`
- [x] Docker MCP pre-configured via `holyclaude/claude.json` mount
- [x] `OPENAI_API_BASE_URL` + `OPENAI_API_KEY` added to compose — all LiteLLM routes (Gemini, Copilot, local) accessible by model name
- [x] `task-master-ai@0.43.1` installed globally in container (persists in home volume)
- [x] Cursor login documented as manual UI setup
- [x] Gemini CLI auth documented (`gemini auth` in HolyClaude terminal — separate from CLIProxyAPI OAuth)
- [ ] `gemini auth` not yet run — user needs to complete Google OAuth inside HolyClaude terminal

### SearXNG
- [x] SearXNG private metasearch at `localhost:8080` — added to main `docker-compose.yml`
- [x] JSON API enabled (`formats: [html, json]`) for Open WebUI integration
- [x] `searxng/settings.yml` and `searxng/uwsgi.ini` committed
- [x] Wired into Open WebUI (Admin → Settings → Web Search → SearXNG → `http://searxng:8080`)
- [x] Browser integration documented (Chrome/Brave: use `http://localhost:8080/search?q=%s` as search engine URL)

### LiteLLM spend/usage tracking
- [x] `disable_spend_logs: false` confirmed in `litellm-config.yaml`
- [x] Token counts logging to Postgres and visible in LiteLLM admin UI (`/ui` → Usage tab)
- [ ] Add pricing data (`input_cost_per_token` / `output_cost_per_token`) to model entries if dollar estimates are wanted

### Host Claude Code wiring
- [x] `ANTHROPIC_API_KEY` (virtual key) + `ANTHROPIC_BASE_URL=http://localhost:4000` set in `~/.claude/settings.json`
- [x] `/logout` run in Claude Code so env vars take precedence over OAuth session
- [x] Confirmed working: `POST /v1/messages 200 OK` in LiteLLM logs

---

## 📋 Backlog

### OpenUsage custom plugin (next up)
Fork `plugins/claude/plugin.js` in [openusage](https://github.com/robinebers/openusage) to read from `~/.local-ai/anthropic-token.json` instead of `~/.claude/.credentials.json`. A `launchd` agent syncs the token from the `cli-proxy-api` Docker container to that path every 15 minutes. Build openusage locally from source. See README "OpenUsage compatibility" for full constraint explanation.

### Openwork (after openusage)
[openwork](https://github.com/different-ai/openwork) — OpenCode-related, may work with this stack. Evaluate after the openusage fork is complete.

### OpenUsage — Anthropic usage visibility (archived detail)
openusage reads `~/.claude/.credentials.json` for the OAuth token. Claude Code also reads that file on startup and uses it to bypass the proxy — so we can't keep credentials there permanently.

**Planned fix:** Fork `plugins/claude/plugin.js` in openusage to read from `~/.local-ai/anthropic-token.json` instead. A `launchd` agent syncs the token from the `cli-proxy-api` Docker container to that custom path every 15 minutes. Requires building openusage locally.

**Interim workaround:** `scripts/sync-openusage-token.sh` — temporarily writes credentials for a manual check, then `--cleanup` removes them. Do not start a new Claude Code session while the file exists.

See README "OpenUsage compatibility" section for full detail.

### Open WebUI — Docker MCP integration
Open WebUI supports MCP via HTTP/SSE endpoints. Docker MCP (`docker mcp gateway run`) is stdio-based.
To bridge these, a sidecar like `supergateway` could wrap stdio → SSE, or check if Docker Desktop exposes an HTTP MCP gateway port.
For now, Docker MCP is available in HolyClaude (where it's more useful for coding workflows).

### Open WebUI — configure default model
Set a default model in Open WebUI admin settings so new conversations don't require model selection.

### Copilot Enterprise token refresh automation
Currently manual: `gh auth refresh && gh auth token` → update `.env` → restart litellm.
Could be scripted as a cron job or a small refresh helper.

### Gemini via Copilot Enterprise API
Gemini is enabled in the Copilot org settings UI but was not accessible via the `api.githubcopilot.com` REST endpoint. May become available via API in future — worth retesting periodically.

### LM Studio model alias verification
Once LM Studio server is running (`Settings → Server → Start`):
```bash
curl http://localhost:1234/v1/models
```
Compare against the `local/*` entries in `litellm-config.yaml`. Update any that don't match.

### Git repo publishing
Clean up for GitHub as a public blueprint:
- Verify no secrets in git history
- Add a license
- Tag v1.0

---

## Architecture

```
localhost:3000  ──→  Open WebUI
localhost:3001  ──→  HolyClaude (Claude Code workstation, separate compose)
                          │
localhost:4000  ──→  LiteLLM Proxy
                          │
                          ├──→  claude-sonnet/opus/haiku  ──→  CLIProxyAPI :8317  ──→  Anthropic OAuth
                          │       (fallback: copilot/claude-* → local/*)
                          │
                          ├──→  gemini-flash/pro  ──→  CLIProxyAPI :8317  ──→  Google OAuth
                          │
                          ├──→  copilot/*  ──→  api.githubcopilot.com  (COPILOT_TOKEN)
                          │
                          ├──→  github/*  ──→  GitHub Models free tier  (GITHUB_API_KEY)
                          │
                          └──→  local/* / lm-studio/*  ──→  LM Studio :1234

                     PostgreSQL (internal — no host port)

All host ports bound to 127.0.0.1 only.
Docker MCP available on host (Claude Code) and in HolyClaude via socket mount.
```
