#!/usr/bin/env bash
# backup_db.sh — SQLite database backup with retention policy
# Usage: ./scripts/backup_db.sh [--db <path>] [--backup-dir <path>] [--keep <days>]
# Recommended: run via cron  0 2 * * * /path/to/backup_db.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

DB_PATH="${PROJECT_DIR}/data/trading.db"
BACKUP_DIR="${PROJECT_DIR}/data/backups"
KEEP_DAYS=30
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

# ------------------------------------------------------------------
# Parse arguments
# ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      DB_PATH="$2"; shift 2 ;;
    --backup-dir)
      BACKUP_DIR="$2"; shift 2 ;;
    --keep)
      KEEP_DAYS="$2"; shift 2 ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: $0 [--db <path>] [--backup-dir <path>] [--keep <days>]"
      exit 1 ;;
  esac
done

# ------------------------------------------------------------------
# Validate source DB
# ------------------------------------------------------------------
if [[ ! -f "${DB_PATH}" ]]; then
  echo "[WARN] Database not found: ${DB_PATH} — nothing to back up."
  exit 0
fi

mkdir -p "${BACKUP_DIR}"

BACKUP_FILE="${BACKUP_DIR}/trading_${TIMESTAMP}.db"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting backup..."
echo "  Source  : ${DB_PATH}"
echo "  Dest    : ${BACKUP_FILE}"
echo "  Retain  : ${KEEP_DAYS} days"

# ------------------------------------------------------------------
# Hot backup using SQLite .backup command (safe with open DB)
# ------------------------------------------------------------------
sqlite3 "${DB_PATH}" ".backup '${BACKUP_FILE}'"

if [[ -f "${BACKUP_FILE}" ]]; then
  SIZE="$(du -sh "${BACKUP_FILE}" | cut -f1)"
  echo "  Backup completed — size: ${SIZE}"
else
  echo "[ERROR] Backup file not created."
  exit 1
fi

# Compress backup
gzip -f "${BACKUP_FILE}"
echo "  Compressed: ${BACKUP_FILE}.gz"

# ------------------------------------------------------------------
# Pruning — remove backups older than KEEP_DAYS
# ------------------------------------------------------------------
echo "  Pruning backups older than ${KEEP_DAYS} days..."
PRUNED=0
while IFS= read -r -d '' old_file; do
  rm -f "${old_file}"
  echo "  Deleted: ${old_file}"
  PRUNED=$((PRUNED + 1))
done < <(find "${BACKUP_DIR}" -maxdepth 1 -name "trading_*.db.gz" -mtime "+${KEEP_DAYS}" -print0)

echo "  Pruned ${PRUNED} old backup(s)."

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
TOTAL="$(find "${BACKUP_DIR}" -maxdepth 1 -name "trading_*.db.gz" | wc -l | tr -d ' ')"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup done. Total backups retained: ${TOTAL}"
