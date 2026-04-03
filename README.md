# local-ai

A fully local, security-hardened AI proxy stack that gives you a **single OpenAI-compatible endpoint** routing to every model you have access to — enterprise, cloud, and local — from one interface.

## What it does

Instead of configuring every tool (IDE extensions, Claude Code, scripts) with separate API keys and base URLs for each provider, you point them all at `http://localhost:4000`. The stack routes requests to the right backend automatically.

```
Your tools  →  LiteLLM :4000  →  GitHub Copilot Enterprise  (frontier models via PAT)
                               →  Anthropic Claude           (OAuth via CLIProxyAPI)
                               →  Google Gemini              (OAuth via CLIProxyAPI)
                               →  LM Studio :1234            (any local model, JIT loaded)
```

## Why

- **One key to rule them all** — every tool uses the same `LITELLM_MASTER_KEY` against `localhost:4000`
- **Offline fallback** — when you hit session limits or lose connectivity, requests route to local models in LM Studio automatically
- **No credentials scattered** — GitHub PAT, OAuth sessions, and Postgres password all live in a gitignored `.env`; nothing sensitive is committed
- **Completely local inbound** — all ports are bound to `127.0.0.1`. Nothing on your LAN or VPN can reach this stack
- **Immutable images** — both service images are pinned by digest, not just tag, so nothing changes under you

## Stack

| Service | Image | Port | Purpose |
|---|---|---|---|
| **LiteLLM** | `ghcr.io/berriai/litellm` v1.83.0 | `:4000` | Proxy + admin UI |
| **CLIProxyAPI** | `eceasy/cli-proxy-api` | `:8317` | OAuth bridge for Gemini + Claude |
| **PostgreSQL** | `postgres:16-alpine` | internal only | LiteLLM backend DB |

## Prerequisites

- Docker Desktop
- LM Studio running locally on port `1234` (for local model routing)
- A GitHub account with Copilot Enterprise access

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
| `GITHUB_API_KEY` | github.com → Settings → Developer settings → Fine-grained tokens → `models: Read` scope. If your org uses SSO, authorize the token for your org after creation. |
| `POSTGRES_PASSWORD` | `openssl rand -hex 16` |
| `CLIPROXY_MANAGEMENT_KEY` | Any strong string — also set this same value in `cliproxyapi/config.yaml` under `remote-management.secret-key` |

### 2. Set your CLIProxyAPI management key

Open `cliproxyapi/config.yaml` and set:

```yaml
remote-management:
  secret-key: "your-key-here"   # same value as CLIPROXY_MANAGEMENT_KEY in .env
```

Then tell git to stop tracking your local changes to this file (so your key is never committed):

```bash
git update-index --skip-worktree cliproxyapi/config.yaml
```

### 3. Start the stack

```bash
docker compose up -d
```

Postgres starts first, then LiteLLM waits for it to be healthy before starting. CLIProxyAPI starts independently.

### 4. Log into providers via CLIProxyAPI

Open **http://localhost:8317/management.html** and enter your management key.

From there, use the UI to authenticate:
- **Gemini** — Google OAuth (opens a browser flow)
- **Claude** — Anthropic OAuth (opens a browser flow)

Credentials are saved to a named Docker volume and survive restarts. You only need to redo this when tokens expire.

### 5. Verify

```bash
# LiteLLM health
curl http://localhost:4000/health/liveliness

# List available models (use your master key)
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer sk-local-YOUR_MASTER_KEY"
```

LiteLLM admin UI: **http://localhost:4000/ui** — log in with your master key.

### 6. Wire up your tools

Anywhere you configure an OpenAI-compatible client:

```
Base URL:  http://localhost:4000/v1
API Key:   <your LITELLM_MASTER_KEY>
```

**Claude Code** — set `ANTHROPIC_BASE_URL` in `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:4000"
  }
}
```

## Model reference

| Model name to use | Routes to |
|---|---|
| `github/gpt-4o` | GitHub Copilot Enterprise |
| `github/gpt-4o-mini` | GitHub Copilot Enterprise |
| `github/o1`, `github/o3-mini` | GitHub Copilot Enterprise |
| `github/claude-3.5-sonnet`, `github/claude-3.7-sonnet` | GitHub Copilot Enterprise |
| `github/llama-3.3-70b`, `github/mistral-large` | GitHub Copilot Enterprise |
| `claude-opus`, `claude-sonnet`, `claude-haiku` | Anthropic via CLIProxyAPI OAuth |
| `gemini-flash`, `gemini-pro` | Google via CLIProxyAPI OAuth |
| `lm-studio/<model-id>` | LM Studio (JIT loaded) |

For LM Studio, use the exact model ID shown in LM Studio's UI, e.g.:
`lm-studio/lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF`

To see what's available: `curl http://localhost:1234/v1/models`

## Managing the stack

```bash
# Start
docker compose up -d

# Stop (data is preserved in Docker volumes)
docker compose down

# View logs
docker compose logs -f litellm
docker compose logs -f cli-proxy-api

# Restart a single service
docker compose restart litellm

# Full reset (WARNING: destroys Postgres data and OAuth credentials)
docker compose down -v
```

## Security notes

- **Inbound**: all ports bound to `127.0.0.1` — nothing outside this machine can connect
- **Images**: pinned by SHA256 digest — tags cannot be silently re-pointed to a different image
- **Secrets**: `.env` is gitignored; `cliproxyapi/config.yaml` is marked `skip-worktree` after you add your management key
- **Telemetry**: disabled in both LiteLLM config and environment — no usage data is sent to LiteLLM's servers
- **Callbacks**: `success_callback` and `failure_callback` are explicitly empty — prompts and responses are never forwarded to external services
- **Containers**: `no-new-privileges` and `cap_drop: ALL` on both service containers
- **Database**: Postgres is not exposed on any host port — only reachable from within the Docker network

## Updating

Images are pinned by digest intentionally. To update to a new version:

1. Find the new image digest (e.g. `docker buildx imagetools inspect ghcr.io/berriai/litellm:vX.Y.Z-nightly`)
2. Update the `image:` line in `docker-compose.yml`
3. `docker compose pull && docker compose up -d`

For LiteLLM specifically: check the [release notes](https://github.com/BerriAI/litellm/releases) for breaking changes before updating, as the proxy config format occasionally changes between minor versions.
