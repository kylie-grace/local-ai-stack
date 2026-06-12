# local-ai Stack Roadmap

## ✅ Completed — June 2026 Upgrade

### Stack
- [x] LiteLLM updated to latest digest (main-latest, sha256:7c311546…)
- [x] Open WebUI updated to latest digest (sha256:7f1b0a1a…)
- [x] CLIProxyAPI removed entirely (along with OAuth volumes and ports)
- [x] Orphan containers (holy-claude, cli-proxy-api) cleaned up

### Model Routing
- [x] All GitHub Models and Copilot Enterprise models removed
- [x] All CLIProxyAPI OAuth routes removed (Claude, Gemini)
- [x] UM GPT Toolkit added: 27 models with full per-token pricing
  - Claude: claude-sonnet-4-6, claude-opus-4-6/4-7, claude-haiku-4-5
  - OpenAI: gpt-4o/mini, gpt-4.1/mini/nano, gpt-5/mini/5.1/5.2/5.4/5.5
  - Reasoning: o1, o3, o3-mini, o4-mini
  - Image: gpt-image-1.5, gpt-image-2
  - Embeddings: text-embedding-3-large/small
  - Google: gemini-3-flash-preview, gemini-3.1-flash-image-preview
  - Meta: llama-4-maverick, llama-4-scout
- [x] LM Studio: 4 new models added (qwen3.6-35b, gemma4-31b, nomic-embed-v2, nomic-embed-v1.5)
- [x] All 13 existing LM Studio models confirmed in config
- [x] Embedding fallbacks: local Nomic → umgpt cloud if LM Studio is off
- [x] Router failovers: umgpt → local for Claude, GPT-5, o3

### Infrastructure
- [x] Docker MCP gateway running as launchd service (auto-start on login)
  - `com.local-ai.mcp-gateway` — Streamable HTTP (streaming) mode on port 8811
  - Open WebUI connects via `http://mcp-proxy:8080/mcp` (nginx proxy rewrites Host header)
  - Logs: `/tmp/mcp-gateway.log`
- [x] .env cleaned up (removed GitHub/Copilot/CLIProxy keys, added UMGPT keys)
- [x] .env.example updated to reflect new stack

### Data & Tracking
- [x] 67 Claude conversations imported into Open WebUI (tagged `imported-claude`)
- [x] LiteLLM virtual budget keys created:
  - `umgpt-monthly` — $250/mo limit (key stored locally; not committed)
  - `claude-code-tracking` — $100/mo limit, for Phase 13 (key stored locally; not committed)
- [x] Codexbar quota-shim running as launchd service (com.local-ai.quota-shim, port 4001)
  - Translates Codexbar's `GET /v1/quota-stats` → LiteLLM `/key/info` + `/spend/logs`
  - Returns spend, token counts (input/output/total), and reset date in Codexbar's expected format
  - Both virtual keys confirmed working: umgpt-monthly ($250) and claude-code-tracking ($100)
- [x] Codexbar: set Enterprise Host → `http://localhost:4001/v1`, add both virtual keys as accounts
- [x] LiteLLM guardrails: `hide-secrets` (post-call, `default_on: true`) active in litellm-config.yaml

### Open WebUI Configuration
- [x] MCP gateway switched to `--transport streaming` (Streamable HTTP, port 8811)
  - Auth: `MCP_GATEWAY_AUTH_TOKEN` set in launchd plist (`com.local-ai.mcp-gateway`)
  - Gateway URL: `http://localhost:8811/mcp` (host), `http://mcp-proxy:8080/mcp` (container)
- [x] mcp-proxy: `nginxinc/nginx-unprivileged:alpine` on litellm-net
  - Rewrites `Host: host.docker.internal:8811` → `Host: localhost:8811` (required by gateway)
- [x] Tool server: `type: mcp`, URL `http://mcp-proxy:8080/mcp`, Bearer auth
- [x] Embedding: engine `openai`, model `local/nomic-embed-v1.5` (LiteLLM), chunk 1500/100
- [x] Image generation: enabled, engine `openai`, model `umgpt/gpt-image-2` via LiteLLM
- [x] Default model: `umgpt/claude-sonnet-4-6`
- [x] API keys enabled (`ENABLE_API_KEYS=true` — plural — in docker-compose.yml)
  - Pre-created key stored locally (not committed)
- [x] gmail-mcp added to `~/.docker/mcp/registry.yaml`

---

## 🔲 Pending — One-Time UI Setup (Do in Browser)

Open http://localhost:3000 → Admin panel:

- [x] **Memory**: enabled
- [x] **Web Search**: confirmed (SearXNG at `http://searxng:8080` configured in DB)
- [x] **Tool server**: Docker MCP connected, 10 tools available
- [x] **Image generation**: configured (user fixed in UI)

---

## ✅ Phase 13 — Claude Code Routing (Session-Break)

> Routes Claude Code → LiteLLM → UM GPT Toolkit for spend tracking under the
> `claude-code-tracking` virtual key. Config is in place; activate by editing
> `~/.claude/settings.json` (takes effect on the NEXT Claude Code session).

**Key insight:** UM GPT serves a **native Anthropic `/v1/messages` endpoint**, so the
Claude Code aliases use the `anthropic/` provider for clean passthrough — NOT `openai/`.
The earlier `openai/` approach forced an Anthropic→OpenAI→Anthropic conversion that
double-emitted `message_start` on streams and broke Claude Code's SSE parser. Do **not**
set `LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES` — that forces the broken path.

1. Claude Code aliases in `litellm-config.yaml` use `anthropic/<model>` + `api_base`
   `https://api.toolkit.umgpt.umich.edu` (no `/v1`). UM GPT/Portkey requires
   `Authorization: Bearer`, so each alias sets `extra_headers.Authorization` to
   `os.environ/UMGPT_CLAUDE_CODE_AUTH` (the full `Bearer <key>` string lives in `.env`).
2. Ensure `UMGPT_CLAUDE_CODE_AUTH=Bearer <key>` is in `.env`, then **recreate** (not just
   restart) LiteLLM so the new env var loads: `docker compose up -d litellm`.
3. Verify through LiteLLM (streaming must show exactly ONE `message_start`):
   ```bash
   curl -sN http://localhost:4000/v1/messages \
     -H "x-api-key: <claude-code-tracking key>" \
     -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" \
     -d '{"model":"claude-sonnet-4-6","max_tokens":15,"stream":true,
          "messages":[{"role":"user","content":"hi"}]}' | grep '^event:'
   ```
4. Cut over `~/.claude/settings.json` (use the `claude-code-tracking` virtual key, not
   the UMGPT key):
   ```json
   { "env": { "ANTHROPIC_BASE_URL": "http://localhost:4000",
              "ANTHROPIC_AUTH_TOKEN": "<claude-code-tracking virtual key>" } }
   ```
5. Confirm spend lands on the key: `GET http://localhost:4000/key/info?key=<key>` (master auth).
6. Rollback: restore `ANTHROPIC_BASE_URL=https://api.toolkit.umgpt.umich.edu` and the
   UMGPT key in `~/.claude/settings.json` — Claude Code goes back to hitting UM GPT directly.

---

## 🔲 Backlog

- [ ] **Google Drive MCP** — needs Google Cloud OAuth app + `@modelcontextprotocol/server-gdrive` (PLAN.md Phase 5)
- [ ] **OnlyOffice** — optional document editor with LiteLLM AI integration (PLAN.md Phase 10)
- [ ] **Claude history re-import** — if Anthropic ever exports project conversation history
- [ ] **LM Studio model ID sync** — verify IDs with `curl http://localhost:1234/v1/models` after loading new models

---

## Reference

| Service | URL | Notes |
|---|---|---|
| LiteLLM proxy + UI | http://localhost:4000 / http://localhost:4000/ui | Login with LITELLM_MASTER_KEY |
| Codexbar quota shim | http://localhost:4001/v1 | llmproxy enterprise host |
| Open WebUI | http://localhost:3000 | Chat interface |
| SearXNG | http://localhost:8080 | Private search |
| MCP gateway | http://localhost:8811/mcp | Streamable HTTP endpoint for tools |
| Spend dashboard | http://localhost:4000/ui → Usage | Filter by key/model/date |
