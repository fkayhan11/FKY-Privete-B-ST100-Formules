#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   bash scripts/run_dividend_refresh.sh
#   SLEEP_SECONDS=0.8 MAX_ITEMS=8 bash scripts/run_dividend_refresh.sh
#
# Behavior:
# - STRICT=0 (default): if KAP is unreachable, keep current snapshot and exit gracefully.
# - STRICT=1: fail with non-zero exit.

SLEEP_SECONDS="${SLEEP_SECONDS:-0.8}"
MAX_ITEMS="${MAX_ITEMS:-8}"
STRICT="${STRICT:-0}"
SCRIPT="scripts/build_dividend_snapshot_kap.py"
SNAPSHOT="data/dividend_snapshot.json"

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

if ! check_python_deps; then
  exit 4
fi

if ! check_kap_dns; then
  echo "[WARN] KAP DNS/network not reachable. Existing dividend snapshot will be kept."
  if [ "${STRICT}" = "1" ]; then
    exit 3
  fi
  if [ -f "${SNAPSHOT}" ]; then
    exit 0
  fi
  # No dividend snapshot yet is acceptable; app will continue with fallback.
  echo "[WARN] No existing dividend snapshot found (${SNAPSHOT}); proceeding without hard fail."
  exit 0
fi

echo "[RUN] dividend refresh sleep=${SLEEP_SECONDS} max_items=${MAX_ITEMS}"
if safe_python_run python3 "${SCRIPT}" --sleep "${SLEEP_SECONDS}" --max-items "${MAX_ITEMS}"; then
  echo "[OK] dividend snapshot refresh completed."
  exit 0
fi

rc=$?
echo "[WARN] dividend snapshot refresh failed (rc=${rc})."
if [ "${STRICT}" = "1" ]; then
  exit "${rc}"
fi
if [ -f "${SNAPSHOT}" ]; then
  echo "[WARN] strict=0 -> existing ${SNAPSHOT} preserved."
  exit 0
fi
echo "[WARN] No existing dividend snapshot found; app will continue with fallback dividend fields."
exit 0
