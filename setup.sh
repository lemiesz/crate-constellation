#!/bin/zsh
# Crate Constellation — first-time setup on a fresh clone.
#
#   ./setup.sh            install everything, build, start the app, open it
#   ./setup.sh --no-open  same, but don't start the server or open a browser
#
# Safe to re-run: every step skips what is already done. It never touches
# rekordbox or any drive. The app itself walks you through importing your
# library and setting up stick exports (Getting started).
set -e
cd "$(dirname "$0")"

step() { print -P "\n%F{yellow}▸%f %B$1%b"; }
ok()   { print -P "  %F{green}✓%f $1"; }
fail() { print -P "  %F{red}✗%f $1"; exit 1; }

step "Checking requirements"
[[ "$(uname)" == "Darwin" ]] || fail "macOS only (rekordbox and the deck tooling are Mac-side)."

PY=""
for cand in python3.14 python3.13 python3.12 python3; do
  if command -v $cand >/dev/null 2>&1 &&
     $cand -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    PY=$cand; break
  fi
done
[[ -n "$PY" ]] || fail "Python 3.12+ not found. Install it: brew install python@3.14"
ok "Python: $($PY --version)"

command -v node >/dev/null 2>&1 || fail "Node.js not found. Install it: brew install node"
[[ $(node -p 'process.versions.node.split(".")[0]') -ge 20 ]] || fail "Node 20+ needed (have $(node -v))."
ok "Node: $(node -v)"

if [[ -f "$HOME/Library/Pioneer/rekordbox/master.db" ]]; then
  ok "rekordbox library found"
else
  print "  ! rekordbox library not found — install rekordbox 6/7 and import your music"
  print "    before the app's import step. Setup continues anyway."
fi

step "Python environment"
[[ -d .venv ]] || $PY -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
ok "engine dependencies installed"

step "Frontend"
(cd app && { [[ -d node_modules ]] && npm install --silent || npm ci --silent; })
ok "app dependencies installed"
(cd app && npm run build --silent >/dev/null)
ok "app built"

if [[ "$1" == "--no-open" ]]; then
  step "Done"
  print "  Start the app with ./run.sh, then open http://localhost:8765"
  exit 0
fi

step "Starting Crate Constellation"
if curl -s -m 2 localhost:8765/api/health >/dev/null; then
  ok "already running on :8765"
else
  nohup ./run.sh > nohup.out 2>&1 &
  for i in {1..30}; do
    curl -s -m 1 localhost:8765/api/health >/dev/null && break
    sleep 1
  done
  curl -s -m 2 localhost:8765/api/health >/dev/null || fail "server didn't start — see nohup.out"
  ok "running on http://localhost:8765 (log: nohup.out)"
fi
open http://localhost:8765
print "\n  The app opens on Getting started: it imports your library and sets up stick exports."
