#!/usr/bin/env bash
set -euo pipefail

# Light build: the workflow is a stdlib-only wrapper around the `edisclosure`
# CLI, so there is no venv, no pip, and no vendored `lib/` — just copy the
# sources and zip them.

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$ROOT_DIR/dist"
WORK_DIR="$DIST_DIR/workflow"
OUTPUT="$DIST_DIR/alfred-e-disclosure.alfredworkflow"

rm -rf "$WORK_DIR" "$OUTPUT"
mkdir -p "$WORK_DIR"

cp "$ROOT_DIR"/list_reports.py "$WORK_DIR"/
cp "$ROOT_DIR"/action.py "$WORK_DIR"/
cp "$ROOT_DIR"/edisclosure_bin.py "$WORK_DIR"/
cp "$ROOT_DIR"/relative_time_ru.py "$WORK_DIR"/
cp "$ROOT_DIR"/info.plist "$WORK_DIR"/
cp "$ROOT_DIR"/icon.png "$WORK_DIR"/

chmod +x "$WORK_DIR"/list_reports.py "$WORK_DIR"/action.py

(cd "$WORK_DIR" && zip -rq "$OUTPUT" .)

echo "Workflow bundle created at: $OUTPUT"
open "$OUTPUT"
