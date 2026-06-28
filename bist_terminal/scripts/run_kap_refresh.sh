#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   bash scripts/run_kap_refresh.sh am
#   bash scripts/run_kap_refresh.sh pm
#   bash scripts/run_kap_refresh.sh all
#
# Defaults target low-rate chunked KAP refresh:
# - 10-symbol chunks
# - sleep ~1.2s
# - max-candidates=3
#
# Behavior:
# - STRICT=0 (default): if KAP is unreachable, keep current snapshot and exit gracefully.
# - STRICT=1: fail with non-zero exit.

MODE="${1:-all}"
SLEEP_SECONDS="${SLEEP_SECONDS:-1.2}"
MAX_CANDIDATES="${MAX_CANDIDATES:-3}"
POLICY_HISTORY="${POLICY_HISTORY:-1}"
RUN_DIVIDEND_REFRESH="${RUN_DIVIDEND_REFRESH:-1}"
STRICT="${STRICT:-0}"
SCRIPT="scripts/build_fundamentals_snapshot_kap.py"
MAIN_SNAPSHOT="data/fundamentals_snapshot.json"
chunk_failures=0

check_python_deps() {
  python3 - <<'PY'
import importlib.util
import sys
missing = [m for m in ("requests", "bs4") if importlib.util.find_spec(m) is None]
if missing:
    print("[ERROR] Missing Python modules:", ", ".join(missing))
    print("[HINT] Install with: python3 -m pip install requests beautifulsoup4")
    raise SystemExit(4)
print("[OK] Python deps ready: requests, bs4")
PY
}

check_kap_dns() {
  python3 - <<'PY'
import socket
try:
    socket.getaddrinfo("www.kap.org.tr", 443)
except OSError:
    print("[WARN] KAP DNS resolve failed: www.kap.org.tr")
    raise SystemExit(3)
print("[OK] KAP DNS reachable: www.kap.org.tr")
PY
}

safe_python_run() {
  local rc=0
  set +e
  "$@"
  rc=$?
  set -e
  return "${rc}"
}

run_chunk() {
  local start="$1"
  local count="$2"
  local extra=()
  if [ "${POLICY_HISTORY}" = "1" ]; then
    extra+=(--policy-history)
  fi
  echo "[RUN] start=${start} count=${count} sleep=${SLEEP_SECONDS} candidates=${MAX_CANDIDATES} policy=${POLICY_HISTORY}"
  if safe_python_run python3 "${SCRIPT}" --start "${start}" --count "${count}" --sleep "${SLEEP_SECONDS}" --max-candidates "${MAX_CANDIDATES}" "${extra[@]}"; then
    return 0
  fi

  local rc=$?
  chunk_failures=$((chunk_failures + 1))
  echo "[WARN] fundamentals chunk failed (start=${start}, count=${count}, rc=${rc})"
  if [ "${STRICT}" = "1" ]; then
    return "${rc}"
  fi
  if [ -f "${MAIN_SNAPSHOT}" ]; then
    echo "[WARN] strict=0 -> existing ${MAIN_SNAPSHOT} preserved."
    return 0
  fi
  return "${rc}"
}

run_range() {
  local from="$1"
  local to="$2"
  local cur="${from}"
  while [ "${cur}" -lt "${to}" ]; do
    run_chunk "${cur}" 10
    cur=$((cur + 10))
  done
}

if ! check_python_deps; then
  exit 4
fi

if ! check_kap_dns; then
  echo "[WARN] KAP DNS/network not reachable. Existing snapshot will be kept."
  if [ "${STRICT}" = "1" ]; then
    exit 3
  fi
  if [ -f "${MAIN_SNAPSHOT}" ]; then
    exit 0
  fi
  echo "[ERROR] No existing fundamentals snapshot found at ${MAIN_SNAPSHOT}."
  exit 3
fi

case "${MODE}" in
  am)
    run_range 0 50
    ;;
  pm)
    run_range 50 100
    ;;
  all)
    run_range 0 100
    ;;
  *)
    echo "Unknown mode: ${MODE}"
    echo "Use one of: am | pm | all"
    exit 1
    ;;
esac

echo "[OK] KAP refresh mode '${MODE}' completed."
if [ "${chunk_failures}" -gt 0 ]; then
  echo "[WARN] ${chunk_failures} chunk(s) failed during fundamentals refresh."
  if [ "${STRICT}" = "1" ]; then
    exit 2
  fi
fi

if [ "${RUN_DIVIDEND_REFRESH}" = "1" ]; then
  echo "[RUN] dividend snapshot refresh"
  if ! bash scripts/run_dividend_refresh.sh; then
    if [ "${STRICT}" = "1" ]; then
      exit 2
    fi
    echo "[WARN] dividend refresh failed, continuing with existing data."
  fi
fi
