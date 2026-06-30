#!/bin/bash
# Cron job for periodic TG parser run
# Add to crontab: 0 */4 * * * /path/to/run_tg_parser.sh >> /tmp/tg_parser.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Activate venv
if [ -d "$PROJECT_DIR/.venv" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
elif [ -d "$PROJECT_DIR/venv" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
fi

cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR"

# Load env
if [ -f "$PROJECT_DIR/bot/.env" ]; then
    set -a
    source "$PROJECT_DIR/bot/.env"
    set +a
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Starting TG parser..."

# Run TG parser
python scripts/parse_tg_channels.py 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') TG parser finished"

echo "$(date '+%Y-%m-%d %H:%M:%S') Starting GdeBenz parser..."

# Run GdeBenz parser (real-time fuel availability, 30+ cities)
python scripts/parse_gdebenz_fast.py 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') GdeBenz parser finished"
echo "---"
