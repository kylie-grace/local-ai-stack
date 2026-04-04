# Incident & Repair Log

## 2026-04-04 — Stack Crash & Claude Code Recovery

### What Happened

1. Claude Code's `ANTHROPIC_BASE_URL` was changed to point at the local LiteLLM proxy (`http://localhost:4000`).
2. On the first call, LiteLLM crashed. Root cause: **Docker's internal VM disk was at 94% capacity**. PostgreSQL panicked mid-write:
   ```
   PANIC: could not write to file "pg_logical/replorigin_checkpoint.tmp": No space left on device
   ```
3. Postgres entered a crash-recovery loop, LiteLLM lost its DB connection, and the whole stack went down.
4. `ANTHROPIC_BASE_URL` was rolled back to the real Anthropic endpoint to keep working in the meantime.

---

### Fix 1 — Free Docker Disk Space

Docker's internal VM was full from accumulated dangling images, old build cache, and unused volumes from dead stacks (`ai-audio-studio`, `infra`, `intermapper-alerts`, `iceoutbridge`, etc.).

**Steps taken:**
- `docker image prune -f` — removed all dangling (untagged) images
- `docker builder prune -f` — cleared build cache
- Manually removed containers from dead stacks (all stopped, non-essential):
  - MCP tool containers (`mcp/playwright`, `mcp/context7`, etc.)
  - `holy-claude`, `25live-watchdog-app`
- Removed their images
- Removed 23 volumes from dead stacks (kept: `local-ai_*`, `n8n_data`, dswtools volume, dc-equipment)

**Result:** Docker VM disk: **94% → 42%** (~29 GB freed)

---

### Fix 2 — Recover PostgreSQL

With disk space freed, Postgres could complete its recovery checkpoint.

```bash
cd ~/dev\ env/local-ai
docker compose restart postgres
# Wait ~15s — came up healthy
docker compose restart litellm
```

Both containers returned to `healthy` status.

---

### Fix 3 — Restore Claude Code Authentication

Claude Code was using a **virtual key** that had been stored in LiteLLM's Postgres DB. That key was lost when Postgres crashed and recovered (the `LiteLLM_VerificationTokenTable` was reset).

**Symptom:**
```
401 {"error":{"message":"Authentication Error, Invalid proxy server token passed...
Unable to find token in cache or LiteLLM_VerificationTokenTable"}}
```

**Fix — Generate a new virtual key:**
```bash
MASTER_KEY=$(grep "^LITELLM_MASTER_KEY=" ~/dev\ env/local-ai/.env | cut -d= -f2)
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"claude-code","duration":null,"models":[]}'
# Returns: {"key": "sk-..."}
```

**Fix — Update `~/.claude/settings.json`:**
```json
{
  "env": {
    "ANTHROPIC_API_KEY": "<new-virtual-key>",
    "ANTHROPIC_BASE_URL": "http://localhost:4000"
  }
}
```

Also had to log out of the Anthropic account in Claude Code so it uses the API key env var instead of the OAuth session.

**Confirmed working:** LiteLLM logs showed `POST /v1/messages 200 OK`.

---

## 2026-04-04 — LiteLLM v1.83 Routing Bugs & Claude Code Tool Failures

Three separate bugs were discovered and fixed in a single session after the stack was restored from the disk crash above. All three required live debugging while Claude Code was active in a second terminal.

---

### Bug 1 — LiteLLM Responses API routing breaks Claude tool calls

**Symptom:** Claude Code worked for one text exchange, then hung or returned empty responses once any tool call occurred. LiteLLM logs showed no errors. The Copilot Enterprise backend logs showed `model claude-sonnet-4.6 does not support Responses API`.

**Root cause:** LiteLLM v1.83.0 introduced an experimental pass-through that routes `/v1/messages` through the **Responses API** (`/responses` endpoint) when the backend provider is detected as `openai`. GitHub Copilot has a `/responses` endpoint, but it does not support Claude models. Pure text messages happened to work; any conversation involving `tool_use`/`tool_result` hit the unsupported path.

The relevant constant in LiteLLM source: `_RESPONSES_API_PROVIDERS = frozenset({"openai"})` in `messages/handler.py`.

**Fix:** Added to `docker-compose.yml` → litellm service environment:
```yaml
LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES: "true"
```
This makes `_should_route_to_responses_api()` always return `False`, keeping all `/v1/messages` traffic on the chat completions path.

---

### Bug 2 — CLIProxyAPI single-chunk streaming silently drops tool arguments

**Symptom:** After switching failover priority so CLIProxyAPI OAuth is primary, tool calls went out with `"input": {}` — every tool was called with an empty parameter object. The model appeared to understand which tool to use, but had no arguments for it.

**Root cause (streaming path):** CLIProxyAPI's OpenAI-compat endpoint (`/v1/chat/completions`) emits all tool call arguments in a **single streaming chunk** (one chunk with both `function.name` and the full `function.arguments` JSON). LiteLLM's `adapters/streaming_iterator.py` treats the chunk that starts a new content block as a "trigger" — it emits a `content_block_start` event but **not** the delta containing the arguments. Arguments were silently dropped.

CLIProxyAPI's native `/v1/messages` (Anthropic format) correctly streams `input_json_delta` events incrementally and is the right path to use.

**Root cause (why wrong path was taken):** The LiteLLM router passes the *alias* model name (e.g. `claude-sonnet`) into `litellm.anthropic_messages`. This overwrites the deployment model name (`anthropic/claude-sonnet-4-6`) from the config. `litellm.get_llm_provider("claude-sonnet")` cannot determine the provider → raises `BadRequestError` → `custom_llm_provider = None` → `anthropic_messages_provider_config = None` → falls through to the completion-transformer path → ends up at `/v1/chat/completions` again.

**Fix:** All CLIProxyAPI-backed models in `litellm-config.yaml` now include:
```yaml
custom_llm_provider: anthropic
api_base: http://cli-proxy-api:8317  # NO /v1 suffix
```
`custom_llm_provider` lives in `litellm_params` and is not overwritten by the incoming request kwargs, so the provider resolves correctly. With provider = anthropic, LiteLLM uses `AnthropicModelInfo.get_complete_url()` which appends `/v1/messages` automatically — hence no `/v1` in `api_base` (it would become `/v1/v1/messages`).

---

### Bug 3 — Config changes require `docker compose restart`, not `docker compose up -d`

**Symptom:** Made changes to `litellm-config.yaml`, ran `docker compose up -d`, tested — old behavior still present. Spent ~15 minutes confused.

**Root cause:** `docker compose up -d` only recreates a container if the *image*, *environment variables*, or *compose configuration* changed. A bind-mounted config file changing on the host does NOT trigger a container restart.

**Fix:** Always use `docker compose restart litellm` after editing `litellm-config.yaml`.

---

### Current Routing Priority (as of 2026-04-04)

The failover priority was flipped (by the other Claude Code session) from the original setup:
- **Primary:** CLIProxyAPI OAuth (Anthropic direct API, daily/hourly limits)  
- **Fallback 1:** Copilot Enterprise (monthly quota, lower throughput ceiling)  
- **Fallback 2:** Local LM Studio model

This is the correct order for heavy agentic use: fresh hourly Anthropic limits are consumed first; the larger monthly Copilot budget acts as overflow.

If Postgres is ever wiped/reset, virtual keys disappear. Just re-run the `key/generate` curl above with the master key and update `settings.json`. The master key itself (`LITELLM_MASTER_KEY` in `.env`) always works and never lives in the DB.

---

### Keeping Disk Healthy

The Docker VM disk fills up from:
- **Old dangling images** — `docker image prune -f` periodically
- **Build cache** — `docker builder prune -f`
- **Unused volumes** — `docker volume prune -f` (be careful — check first)

A good rule of thumb: if `docker system df` shows the VM >80% full, prune before starting heavy workloads.
