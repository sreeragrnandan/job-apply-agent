#!/usr/bin/env bash
# =============================================================================
#  ApplyPilot - One-Click Setup Script
#  Clones and fully sets up the job-apply-agent on any new machine.
#
#  Usage:
#    chmod +x setup.sh && ./setup.sh
#
#  Tip: Place an 'applypilot_content/' folder next to this script
#  with your pre-populated config files and they will be copied
#  automatically to ~/.applypilot/ — no manual editing needed.
#
#  Expected files inside applypilot_content/:
#    .env            (API keys)
#    profile.json    (your personal profile)
#    resume.txt      (plain-text resume)
#    resume.pdf      (PDF resume)
#    searches.yaml   (job search queries)
#    employers.yaml  (Workday employer list)
#    sites.yaml      (direct career sites)
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }

REPO_URL="https://github.com/sreeragrnandan/job-apply-agent.git"
REPO_DIR="job-apply-agent"
NODE_MIN=18

# Resolve the directory where this script lives (works even if called from elsewhere)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTENT_DIR="${SCRIPT_DIR}/applypilot_content"

echo ""
echo -e "${BOLD}${CYAN}================================================${RESET}"
echo -e "${BOLD}${CYAN}       ApplyPilot - Environment Setup           ${RESET}"
echo -e "${BOLD}${CYAN}================================================${RESET}"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
info "Checking prerequisites..."

command -v git &>/dev/null    || error "git not found. Install from https://git-scm.com"
success "git found: $(git --version)"

command -v python3 &>/dev/null || error "python3 not found. Install Python 3.11+ from https://python.org"

python3 - <<'PYCHECK'
import sys
if sys.version_info < (3, 11):
    print(f"Python 3.11+ required. Found {sys.version_info.major}.{sys.version_info.minor}")
    sys.exit(1)
PYCHECK
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
success "Python ${PY_VER} found"

python3 -m pip --version &>/dev/null || error "pip not available. Run: python3 -m ensurepip --upgrade"
success "pip found"

if command -v node &>/dev/null; then
  NODE_VER=$(node -e "process.stdout.write(process.version.replace('v','').split('.')[0])")
  if [ "${NODE_VER}" -ge "${NODE_MIN}" ] 2>/dev/null; then
    success "Node.js v$(node --version | tr -d v) found"
  else
    warn "Node.js ${NODE_VER} found — ${NODE_MIN}+ needed for auto-apply. Upgrade from https://nodejs.org"
  fi
else
  warn "Node.js not found. Auto-apply will not work without it. Install from https://nodejs.org"
fi

# ── 2. Clone or update repo ───────────────────────────────────────────────────
echo ""
info "Setting up repository..."

if [ -d "${REPO_DIR}" ]; then
  warn "Directory '${REPO_DIR}' already exists. Pulling latest changes..."
  cd "${REPO_DIR}"
  git pull origin main 2>/dev/null || git pull origin master
else
  git clone "${REPO_URL}" "${REPO_DIR}"
  cd "${REPO_DIR}"
fi

success "Repository ready at: $(pwd)"

# ── 3. Python virtual environment ─────────────────────────────────────────────
echo ""
info "Setting up Python virtual environment..."

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  success "Created .venv"
else
  info ".venv already exists, skipping creation."
fi

# shellcheck disable=SC1091
source .venv/bin/activate
success "Virtual environment activated"

# ── 4. Install Python dependencies ────────────────────────────────────────────
echo ""
info "Installing applypilot (editable/dev mode)..."
pip install --upgrade pip --quiet
pip install -e ".[dev]" --quiet
success "applypilot installed"

# python-jobspy pins an exact numpy version that breaks pip's resolver.
# --no-deps skips the resolver; the next line installs actual runtime deps.
info "Installing python-jobspy (--no-deps workaround)..."
pip install --no-deps python-jobspy --quiet
pip install pydantic tls-client requests markdownify regex --quiet
success "python-jobspy installed"

# ── 5. Playwright browsers ────────────────────────────────────────────────────
echo ""
info "Installing Playwright Chromium browser..."
python3 -m playwright install chromium
success "Playwright Chromium installed"

# ── 6. Claude Code CLI (auto-apply) ───────────────────────────────────────────
echo ""
if command -v node &>/dev/null; then
  if command -v claude &>/dev/null; then
    success "Claude Code CLI already installed"
  else
    info "Installing Claude Code CLI..."
    npm install -g @anthropic-ai/claude-code 2>/dev/null \
      && success "Claude Code CLI installed" \
      || warn "Could not auto-install Claude Code CLI. Get it from https://claude.ai/code"
  fi
else
  warn "Skipping Claude Code CLI install (Node.js not available)."
fi

# ── 7. Copy config files to ~/.applypilot ────────────────────────────────────
echo ""
ENV_DIR="${HOME}/.applypilot"
mkdir -p "${ENV_DIR}"

if [ -d "${CONTENT_DIR}" ]; then
  info "Found applypilot_content/ — copying your pre-populated config files..."
  cp -rv "${CONTENT_DIR}/." "${ENV_DIR}/"
  success "All files from applypilot_content/ copied to ${ENV_DIR}/"
  echo ""
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${BOLD}Config restored from applypilot_content/${RESET}"
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
  echo -e "  Destination:  ${CYAN}${ENV_DIR}/${RESET}"
  echo -e "  Files copied: $(ls -1 "${CONTENT_DIR}" | tr '\n' '  ')"
  echo ""
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
else
  # Fallback: no pre-populated folder found — copy .env.example
  warn "applypilot_content/ not found next to this script."
  warn "Falling back to .env.example — you will need to fill in your API keys manually."
  ENV_FILE="${ENV_DIR}/.env"
  if [ -f "${ENV_FILE}" ]; then
    warn ".env already exists at ${ENV_FILE} — skipping. Edit manually if needed."
  else
    cp .env.example "${ENV_FILE}"
    success "Copied .env.example → ${ENV_FILE}"
  fi
  echo ""
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${BOLD}ACTION REQUIRED: Add your API keys${RESET}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
  echo -e "  Edit:  ${CYAN}${ENV_FILE}${RESET}"
  echo ""
  echo "  GEMINI_API_KEY=<your key>     # Required  - free at aistudio.google.com"
  echo "  CAPSOLVER_API_KEY=<your key>  # Optional  - CAPTCHA solving during auto-apply"
  echo ""
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
fi

# ── 8. Next steps ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Next steps:${RESET}"
echo ""
if [ -d "${CONTENT_DIR}" ]; then
  echo -e "  ${CYAN}1.${RESET} Verify setup:           ${BOLD}applypilot doctor${RESET}"
  echo -e "  ${CYAN}2.${RESET} Start the pipeline:     ${BOLD}applypilot run${RESET}"
  echo -e "  ${CYAN}3.${RESET} Auto-apply:             ${BOLD}applypilot apply${RESET}"
else
  echo -e "  ${CYAN}1.${RESET} Add your API keys:      ${BOLD}nano ${HOME}/.applypilot/.env${RESET}"
  echo -e "  ${CYAN}2.${RESET} Run the setup wizard:   ${BOLD}applypilot init${RESET}"
  echo -e "  ${CYAN}3.${RESET} Verify setup:           ${BOLD}applypilot doctor${RESET}"
  echo -e "  ${CYAN}4.${RESET} Start the pipeline:     ${BOLD}applypilot run${RESET}"
  echo -e "  ${CYAN}5.${RESET} Auto-apply:             ${BOLD}applypilot apply${RESET}"
fi
echo ""

# ── 9. Doctor ─────────────────────────────────────────────────────────────────
echo -e "${BOLD}Running applypilot doctor...${RESET}"
echo ""
applypilot doctor || warn "Some checks failed. Fix the issues above, then re-run: applypilot doctor"

echo ""
echo -e "${GREEN}${BOLD}Setup complete! Happy job hunting!${RESET}"
echo ""
