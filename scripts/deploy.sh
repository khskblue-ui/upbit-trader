#!/usr/bin/env bash
# deploy.sh — Production deployment script for upbit-trader
# Usage: ./scripts/deploy.sh [--env <path-to-.env>]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
SERVICE_NAME="upbit-trader"
VENV_DIR="${PROJECT_DIR}/.venv"
LOG_DIR="${PROJECT_DIR}/logs"
DB_DIR="${PROJECT_DIR}/data"

# ------------------------------------------------------------------
# Parse arguments
# ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_FILE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: $0 [--env <path-to-.env>]"
      exit 1
      ;;
  esac
done

echo "========================================================"
echo "  upbit-trader Deployment"
echo "========================================================"
echo "  Project : ${PROJECT_DIR}"
echo "  Env file: ${ENV_FILE}"
echo "========================================================"

# ------------------------------------------------------------------
# 1. Validate environment file
# ------------------------------------------------------------------
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[ERROR] .env file not found: ${ENV_FILE}"
  echo "Create one based on .env.example and fill in your credentials."
  exit 1
fi

required_vars=(
  "UPBIT_ACCESS_KEY"
  "UPBIT_SECRET_KEY"
  "TELEGRAM_BOT_TOKEN"
  "TELEGRAM_CHAT_ID"
)

echo "[1/6] Validating environment variables..."
missing=()
# shellcheck source=/dev/null
source "${ENV_FILE}"
for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("$var")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "[ERROR] Missing required environment variables:"
  for v in "${missing[@]}"; do
    echo "  - $v"
  done
  exit 1
fi
echo "  All required variables present."

# ------------------------------------------------------------------
# 2. Check Python & uv
# ------------------------------------------------------------------
echo "[2/6] Checking Python and uv..."
if ! command -v uv &>/dev/null; then
  echo "[ERROR] uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi
echo "  uv: $(uv --version)"

# ------------------------------------------------------------------
# 3. Install / sync dependencies
# ------------------------------------------------------------------
echo "[3/6] Syncing dependencies..."
cd "${PROJECT_DIR}"
uv sync --no-dev
echo "  Dependencies synced."

# ------------------------------------------------------------------
# 4. Run database migrations / initialisation
# ------------------------------------------------------------------
echo "[4/6] Initialising database..."
mkdir -p "${DB_DIR}"
uv run python -c "
import asyncio
from src.data.database import Database

async def init():
    db = Database('sqlite+aiosqlite:///${DB_DIR}/trading.db')
    await db.init()
    await db.close()
    print('  Database initialised.')

asyncio.run(init())
"

# ------------------------------------------------------------------
# 5. Run tests (smoke check)
# ------------------------------------------------------------------
echo "[5/6] Running smoke tests..."
if uv run pytest tests/ -q --tb=short -x 2>&1 | tail -5; then
  echo "  Tests passed."
else
  echo "[ERROR] Tests failed. Aborting deployment."
  exit 1
fi

# ------------------------------------------------------------------
# 6. Install / reload systemd service
# ------------------------------------------------------------------
echo "[6/6] Configuring systemd service..."
mkdir -p "${LOG_DIR}"

SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
if [[ -f "${PROJECT_DIR}/systemd/${SERVICE_NAME}.service" ]]; then
  if sudo cp "${PROJECT_DIR}/systemd/${SERVICE_NAME}.service" "${SYSTEMD_UNIT}"; then
    # Patch WorkingDirectory and EnvironmentFile in the installed unit
    sudo sed -i "s|__PROJECT_DIR__|${PROJECT_DIR}|g" "${SYSTEMD_UNIT}"
    sudo sed -i "s|__ENV_FILE__|${ENV_FILE}|g" "${SYSTEMD_UNIT}"
    sudo sed -i "s|__VENV_DIR__|${VENV_DIR}|g" "${SYSTEMD_UNIT}"

    sudo systemctl daemon-reload
    sudo systemctl enable "${SERVICE_NAME}"
    sudo systemctl restart "${SERVICE_NAME}"
    echo "  Service '${SERVICE_NAME}' enabled and started."
    echo ""
    echo "  Check status : sudo systemctl status ${SERVICE_NAME}"
    echo "  View logs    : journalctl -u ${SERVICE_NAME} -f"
  else
    echo "  [WARN] Could not install systemd unit (no sudo?). Run the service manually:"
    echo "    uv run python -m src.main"
  fi
else
  echo "  [WARN] systemd unit file not found. Starting directly..."
  echo "  Run: uv run python -m src.main"
fi

echo ""
echo "========================================================"
echo "  Deployment complete!"
echo "========================================================"
