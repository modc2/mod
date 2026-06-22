#!/bin/bash
# End-to-end live test of the unified multimodal agent against the REAL Venice
# API. Requires a real Venice key (the media endpoints won't run without one).
#
#   VENICE_KEY=vk-xxxx bash scripts/live_test.sh
#
# It stores the key as BYOK for a deterministic test wallet, then drives the
# /agent SSE endpoint through prompts that force each tool, and downloads the
# produced media to /tmp/venice-live/ so you can eyeball the results.
set -euo pipefail

API="${VENICE_API_PORT:-50880}"
BASE="http://localhost:${API}"
OUT=/tmp/venice-live
mkdir -p "$OUT"

if [ -z "${VENICE_KEY:-}" ]; then
  echo "ERROR: set VENICE_KEY=vk-... (your Venice API key)"; exit 1
fi

cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || echo /root/mod)"

TOKEN=$(python3 -c "import mod as m; print(m.mod('auth')(key='test.venice', crypto_type='ecdsa').token(data={'scope':'venice'}))" 2>/dev/null)
AUTH="Authorization: Bearer $TOKEN"

echo "→ storing BYOK key for test wallet"
curl -s -X POST "$BASE/key" -H "$AUTH" -H 'Content-Type: application/json' -d "{\"key\":\"$VENICE_KEY\"}" >/dev/null

# run_agent "<prompt>" "<attachments json array>"  → prints SSE, saves media
run_agent () {
  local prompt="$1"; local attach="${2:-[]}"
  echo; echo "════ PROMPT: $prompt"
  curl -s -N --max-time 360 -X POST "$BASE/agent" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$prompt\"}],\"attachments\":$attach}" \
  | while IFS= read -r line; do
      case "$line" in
        event:*) ev="${line#event: }";;
        data:*)
          d="${line#data: }"
          case "$ev" in
            status)  echo "   … $(echo "$d" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("text",""))' 2>/dev/null)";;
            message) echo "   💬 $(echo "$d" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("text","")[:200])' 2>/dev/null)";;
            error)   echo "   ✗ $d";;
            media)
              id=$(echo "$d" | python3 -c 'import sys,json;print(json.load(sys.stdin)["media_id"])' 2>/dev/null)
              kind=$(echo "$d" | python3 -c 'import sys,json;print(json.load(sys.stdin)["kind"])' 2>/dev/null)
              curl -s "$BASE/media/$id" -o "$OUT/$id"
              echo "   ✓ $kind  $id  ($(wc -c < "$OUT/$id") bytes) → $OUT/$id"
              LAST_MEDIA="$id"
              ;;
          esac;;
      esac
    done
}

# pick a tool-capable text model from the live list
MODEL=$(curl -s "$BASE/models" | python3 -c "
import sys,json
for m in json.load(sys.stdin).get('data',[]):
    sp=m.get('model_spec',{}).get('capabilities',{})
    if (m.get('type')=='text') and sp.get('supportsFunctionCalling'):
        print(m['id']); break
" 2>/dev/null)
echo "→ orchestrator model: $MODEL"

run_agent "Generate an image of a small bright red circle on a white background."
run_agent "Write one short haiku about the ocean."           # pure-text path
run_agent "Generate an image of a blue square, then upscale it 2x."
run_agent "Generate an image of a green triangle, then make a 5 second video animating it spinning."

echo; echo "Done. Inspect produced media in $OUT/"
