#!/usr/bin/env bash
# =============================================================
#  ApplyPilot — Launch UI (Mac / Linux)
#  chmod +x launch.sh && ./launch.sh
# =============================================================
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo ""
echo " ================================================"
echo "  ApplyPilot UI Launcher"
echo " ================================================"
echo ""

if [ ! -f ".venv/bin/activate" ]; then
  echo "[ERROR] .venv not found. Run ./setup.sh first."
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "[OK]   Virtual environment activated."

python -c "import flask" 2>/dev/null || {
  echo "[INFO] Installing Flask..."
  pip install flask --quiet
  echo "[OK]   Flask installed."
}

echo ""
echo " Starting ApplyPilot UI at http://localhost:5000"
echo " (Your browser will open automatically)"
echo " Press Ctrl+C to stop."
echo ""

python ui/app.py
