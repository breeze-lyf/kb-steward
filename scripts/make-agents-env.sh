#!/usr/bin/env bash
# Build agents.env from the Switch stack's .env without echoing any secret.
# usage: scripts/make-agents-env.sh /path/to/switch/.env [llm-key-file]
set -euo pipefail
SW="$1"; KEYSRC="${2:-}"
get() { grep -E "^$1=" "$2" | head -1 | cut -d= -f2-; }
cp agents.env.example agents.env
sed -i "s|^AGENT_REGISTRATION_TOKEN=.*|AGENT_REGISTRATION_TOKEN=$(get AGENT_REGISTRATION_TOKEN "$SW")|" agents.env
sed -i "s|^MATTERMOST_ADMIN_PASSWORD=.*|MATTERMOST_ADMIN_PASSWORD=$(get MATTERMOST_ADMIN_PASSWORD "$SW")|" agents.env
sed -i "s|^GATEWAY_ADMIN_PASSWORD=.*|GATEWAY_ADMIN_PASSWORD=$(get GATEWAY_ADMIN_PASSWORD "$SW")|" agents.env
sed -i "s|^MATTERMOST_USER_PASSWORD=.*|MATTERMOST_USER_PASSWORD=$(get MATTERMOST_USER_PASSWORD "$SW")|" agents.env
if [ -n "$KEYSRC" ]; then sed -i "s|^LLM_API_KEY=.*|LLM_API_KEY=$(get LLM_API_KEY "$KEYSRC")|" agents.env; fi
chmod 600 agents.env
echo "agents.env written (secrets not shown)"
