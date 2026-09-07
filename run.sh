#!/bin/zsh
# Crate Constellation — start the engine (serves API + built app) on :8765
#
# The engine always runs with --reload. Python imports a module once per
# process, so a long-lived server keeps serving whatever the code said when
# it started: edit the sync engine, click sync, and you get the OLD writer
# with no warning that anything is stale. That cost three test cycles on
# real hardware before it was noticed. --reload watches engine/ and restarts
# on change, so what you click is always what is on disk.
#
# Every sync also records the build that produced it (see /api/version), so a
# stick can always be traced back to the code that wrote it.
cd "$(dirname "$0")"
RELOAD=(--reload --reload-dir engine)
case "$1" in
import)  ./.venv/bin/python engine/import_rekordbox.py ;;
dev)     # hot-reload frontend on :5173 proxying to the engine
         (./.venv/bin/uvicorn engine.server:app --port 8765 $RELOAD &)
         cd app && npm run dev ;;
build)   cd app && npm run build ;;
restart) # kill whatever holds the port, then come back up
         lsof -ti :8765 | xargs -r kill 2>/dev/null
         sleep 1
         lsof -ti :8765 | xargs -r kill -9 2>/dev/null
         exec ./.venv/bin/uvicorn engine.server:app --port 8765 $RELOAD ;;
*)       exec ./.venv/bin/uvicorn engine.server:app --port 8765 $RELOAD ;;
esac
