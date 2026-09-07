#!/bin/zsh
# Crate Constellation — start the engine (serves API + built app) on :8765
cd "$(dirname "$0")"
case "$1" in
import)  ./.venv/bin/python engine/import_rekordbox.py ;;
dev)     # hot-reload frontend on :5173 proxying to the engine
         (./.venv/bin/uvicorn engine.server:app --port 8765 &) 
         cd app && npm run dev ;;
build)   cd app && npm run build ;;
*)       exec ./.venv/bin/uvicorn engine.server:app --port 8765 ;;
esac
