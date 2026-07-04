#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$ROOT_DIR/dist"
WORK_DIR="$DIST_DIR/workflow"
OUTPUT="$DIST_DIR/alfred-e-disclosure.alfredworkflow"

rm -rf "$WORK_DIR" "$OUTPUT"
mkdir -p "$WORK_DIR"

cp "$ROOT_DIR"/list_reports.py "$WORK_DIR"/
cp "$ROOT_DIR"/open_report.py "$WORK_DIR"/
cp "$ROOT_DIR"/save_report.py "$WORK_DIR"/
cp "$ROOT_DIR"/report_cache.py "$WORK_DIR"/
cp "$ROOT_DIR"/refresh_lock.py "$WORK_DIR"/
cp "$ROOT_DIR"/cache_dir.py "$WORK_DIR"/
cp "$ROOT_DIR"/retry_policy.py "$WORK_DIR"/
cp "$ROOT_DIR"/relative_time_ru.py "$WORK_DIR"/
cp "$ROOT_DIR"/tickers.csv "$WORK_DIR"/
cp "$ROOT_DIR"/info.plist "$WORK_DIR"/
cp "$ROOT_DIR"/icon.png "$WORK_DIR"/

# Copy virtual environment dependencies (see README: python3 -m venv .venv).
VENV_DIR="$ROOT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment at $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi
echo "Installing dependencies from requirements.txt ..."
"$VENV_DIR"/bin/pip install --quiet --upgrade pip
"$VENV_DIR"/bin/pip install --quiet -r "$ROOT_DIR"/requirements.txt
mkdir -p "$WORK_DIR"/lib
cp -r "$VENV_DIR"/lib/python*/site-packages/* "$WORK_DIR"/lib/

chmod +x "$WORK_DIR"/list_reports.py "$WORK_DIR"/open_report.py
chmod +x "$WORK_DIR"/save_report.py

(cd "$WORK_DIR" && zip -rq "$OUTPUT" .)

echo "Workflow bundle created at: $OUTPUT"
open "$OUTPUT"
