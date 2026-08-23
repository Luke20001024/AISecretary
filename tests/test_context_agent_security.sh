#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if rg -n --hidden \
  --glob '!.git/**' \
  --glob '!dist/**' \
  --glob '!context-agent/eval/results/**' \
  'sk-[A-Za-z0-9_-]{20,}' . >/dev/null; then
  echo 'Context Agent security: repository contains an API-key-shaped value' >&2
  exit 1
fi

if rg -n 'api\.deepseek\.com|DEEPSEEK_API_KEY|Authorization[[:space:]]*:' chrome-newtab >/dev/null; then
  echo 'Context Agent security: Chrome Dashboard must not call DeepSeek or read credentials' >&2
  exit 1
fi

rg -q 'os\.environ\.get\("DEEPSEEK_API_KEY"\)' context-agent/deepseek_provider.py
rg -q 'KEYCHAIN_SERVICE = "com\.memento\.context-agent\.deepseek-api-key"' context-agent/deepseek_provider.py
rg -q '"/usr/bin/security"' context-agent/deepseek_provider.py
if rg -n "DEEPSEEK_API_KEY[[:space:]]*=[[:space:]]*['\"]" context-agent >/dev/null; then
  echo 'Context Agent security: runtime contains a hard-coded API key assignment' >&2
  exit 1
fi

echo '✓ Context Agent security: no key-shaped value; browser has no provider credential path'
