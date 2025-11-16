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
cp "$ROOT_DIR"/tickers.csv "$WORK_DIR"/
cp "$ROOT_DIR"/info.plist "$WORK_DIR"/
cp "$ROOT_DIR"/icon.png "$WORK_DIR"/

chmod +x "$WORK_DIR"/list_reports.py "$WORK_DIR"/open_report.py

(cd "$WORK_DIR" && zip -rq "$OUTPUT" .)

echo "Workflow bundle created at: $OUTPUT"
