#!/usr/bin/env bash
# One-command launcher for the capstone pipeline (from project root).
#
# Usage:
#   ./start.sh                  # Ollama (if needed) + review web app (port 5001)
#   ./start.sh --with-ingestion # + folder watcher on incoming_scans/
#
# Optional: copy .env.example to .env and adjust (sourced automatically).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -f .venv/bin/activate ]]; then
  echo "Missing .venv in $ROOT"
  echo "Create it with:  python3 -m venv .venv"
  echo "Then:            source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
fi

PORT="${PORT:-5001}"
INGEST_PID=""
OLLAMA_CHILD_PID=""

# Start ollama serve in the background only if nothing is listening on the API port.
maybe_start_ollama() {
  if [[ "${SKIP_OLLAMA:-0}" =~ ^(1|true|yes|on)$ ]]; then
    echo "Skipping ollama (SKIP_OLLAMA=1)."
    return 0
  fi
  # No need to launch the server if LLM extraction is disabled in the app.
  if [[ "${SKIP_LLM:-0}" =~ ^(1|true|yes|on)$ ]]; then
    echo "Skipping ollama (SKIP_LLM=1)."
    return 0
  fi
  if ! command -v ollama &>/dev/null; then
    echo "Note: ollama CLI not in PATH — open the Ollama app or install https://ollama.com for LLM extraction."
    return 0
  fi

  local port="${OLLAMA_PORT:-11434}"
  local url="http://127.0.0.1:${port}/api/tags"
  if curl -sf --connect-timeout 1 "$url" >/dev/null 2>&1; then
    echo "Ollama already running on port ${port}."
    return 0
  fi

  echo "Starting Ollama (ollama serve) on port ${port}..."
  ollama serve >>"${ROOT}/ollama_serve.log" 2>&1 &
  OLLAMA_CHILD_PID=$!

  local i=0
  while [[ $i -lt 30 ]]; do
    if curl -sf --connect-timeout 1 "$url" >/dev/null 2>&1; then
      echo "Ollama is ready."
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "Warning: Ollama did not respond on ${url} in time. Check ollama_serve.log or start the Ollama app."
}

cleanup() {
  if [[ -n "${INGEST_PID}" ]] && kill -0 "${INGEST_PID}" 2>/dev/null; then
    echo ""
    echo "Stopping ingestion watcher (PID ${INGEST_PID})..."
    kill "${INGEST_PID}" 2>/dev/null || true
    wait "${INGEST_PID}" 2>/dev/null || true
  fi
  if [[ -n "${OLLAMA_CHILD_PID}" ]] && kill -0 "${OLLAMA_CHILD_PID}" 2>/dev/null; then
    echo "Stopping Ollama process we started (PID ${OLLAMA_CHILD_PID})..."
    kill "${OLLAMA_CHILD_PID}" 2>/dev/null || true
    wait "${OLLAMA_CHILD_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

maybe_start_ollama

if [[ "${1:-}" == "--with-ingestion" ]] || [[ "${START_INGESTION:-0}" == "1" ]]; then
  echo "Starting ingestion watcher → monitors: ${ROOT}/incoming_scans"
  python ingestion_handler.py &
  INGEST_PID=$!
fi

echo "Starting review app → http://127.0.0.1:${PORT}"
echo "Press Ctrl+C to stop."
export PORT

# Do not use exec if we started background jobs — trap must run cleanup on exit.
if [[ -n "${INGEST_PID}" ]] || [[ -n "${OLLAMA_CHILD_PID}" ]]; then
  python review_app.py
else
  exec python review_app.py
fi
