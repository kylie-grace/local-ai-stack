#!/bin/bash
# Start the Docker MCP gateway in Streamable HTTP (streaming) mode.
#
# Open WebUI connects via the mcp-proxy container (http://mcp-proxy:8080/mcp).
# The mcp-proxy nginx container rewrites the Host header so the gateway's
# DNS-rebinding protection accepts requests from inside Docker.
#
# Claude Code connects directly via stdio: docker mcp gateway run --long-lived
# (no HTTP involved for Claude Code's MCP connection)
#
# In normal operation this is managed by launchd:
#   launchctl load ~/Library/LaunchAgents/com.local-ai.mcp-gateway.plist
#
# Run manually (e.g. for debugging):
#   MCP_GATEWAY_AUTH_TOKEN=<token from .env or plist> ./scripts/start-mcp-gateway.sh

exec /usr/local/bin/docker mcp gateway run --transport streaming --port 8811
