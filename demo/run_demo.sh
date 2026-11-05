#!/usr/bin/env bash
# trust-ladder demo: correct the agent once -> guard compiled -> repeat blocked -> /trust shows the catch.
#
# Real Hermes, real agent loop, real terminal tool, real git (a local bare remote), real plugin.
# Only the model is scripted (demo/scripted_model.py, built on Hermes's own fake provider), so the
# run is reproducible offline with no API key.
#
# Usage:  HERMES_SRC=/path/to/hermes-agent-checkout demo/run_demo.sh
#   needs `hermes` on PATH (installed from that checkout) and git.
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
: "${HERMES_SRC:?set HERMES_SRC to a hermes-agent source checkout}"
WORK="$(mktemp -d /tmp/trust-ladder-demo.XXXXXX)"
export HOME="$WORK/home" HERMES_HOME="$WORK/home/.hermes" NO_COLOR=1 TERM=dumb
# The scripted model listens on loopback; never send that through an HTTP proxy.
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost" no_proxy="${no_proxy:+$no_proxy,}127.0.0.1,localhost"
TYPE_DELAY="${TYPE_DELAY:-0.025}"
mkdir -p "$HERMES_HOME/plugins"

cleanup() {
  hermes gateway stop >/dev/null 2>&1 || true
  [[ -n "${MODEL_PID:-}" ]] && kill "$MODEL_PID" 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------- setup (off camera)
python "$PLUGIN_DIR/demo/scripted_model.py" "$WORK/url" >"$WORK/model.log" 2>&1 &
MODEL_PID=$!
for _ in $(seq 50); do [[ -s "$WORK/url" ]] && break; sleep 0.2; done
python - "$HERMES_HOME" "$(cat "$WORK/url")" <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ["HERMES_SRC"])
from tests.fakes.fake_llm_provider import write_hermes_home
write_hermes_home(Path(sys.argv[1]), sys.argv[2], extra_config=(
    "updates:\n  check: false\ndisplay:\n  compact: false\n"
    "plugins:\n  enabled:\n  - trust-ladder\n"))
PY
ln -sfn "$PLUGIN_DIR" "$HERMES_HOME/plugins/trust-ladder"   # enabled in config.yaml above

git init -q --bare "$WORK/remote.git"
git init -q -b main "$WORK/shop-api"
cd "$WORK/shop-api"
git config user.email dev@example.com && git config user.name dev
echo "def checkout(): ..." > app.py && git add . && git commit -qm "init"
git remote add origin "$WORK/remote.git" && git push -q origin main
echo "# fix: rounding in totals" >> app.py && git commit -qam "fix rounding in totals"
# Warm up before the camera rolls: the first chat auto-starts Hermes's local gateway.
hermes chat -q "warm up" >"$WORK/setup.log" 2>&1 || true
date +%s >"$WORK/t0"

# ---------------------------------------------------------------- helpers
B=$'\e[1m'; C=$'\e[36m'; Y=$'\e[33m'; G=$'\e[32m'; R=$'\e[0m'
say()  { printf '\n%s%s%s\n' "$C$B" "$1" "$R"; sleep 1.2; }
type_cmd() {
  printf '%s$ %s' "$G" "$R"
  local s="$1" i
  for ((i = 0; i < ${#s}; i++)); do printf '%s' "${s:i:1}"; sleep "$TYPE_DELAY"; done
  printf '\n'
}
chat() {
  type_cmd "hermes chat -q \"$1\""
  { hermes chat -q "$1" 2>&1 || true; } | sed -e '/^Resume this session/,$d' -e '/^Query:/d' -e '/^[[:space:]]*$/d'
  sleep 1.5
}

# ---------------------------------------------------------------- the demo (on camera)
printf '\e[H\e[2J'
printf '%strust-ladder%s  correct your Hermes agent once, and it stays corrected\n' "$Y$B" "$R"
printf '   (real Hermes + plugin + git; scripted model so the run is reproducible)\n'
sleep 2

say "1) The agent makes a mistake: it pushes straight to main."
chat "Push my fix to origin"

say "2) You correct it once. The agent records it; trust-ladder compiles a guard and"
printf '%s   proves it catches the mistake and still allows the fix before installing it.%s\n' "$C$B" "$R"
chat "No. Never push straight to main. Push a branch and open a PR."

say "3) A brand-new session. Same request, same old habit..."
echo "# follow-up: currency symbol" >> app.py && git commit -qam "follow-up: currency symbol"
chat "Ship the follow-up fix"
printf '%s   -> guard r1 blocked `git push origin main` BEFORE it ran; the agent got the reason + the fix%s\n' "$Y$B" "$R"
printf '%s      and pushed a branch instead.%s\n' "$Y$B" "$R"
sleep 2.5

say "4) /trust shows the rule, its rung on the ladder, and the catch."
chat "/trust"

say "Proof from the remote: main untouched by the repeat, the fix went to a branch."
type_cmd "git log --oneline --all --decorate"
git --no-pager -C "$WORK/remote.git" log --oneline --all --decorate
sleep 3
printf '\n%sguard > check > rule > human review.  /teach  /trust  /verify%s\n' "$Y$B" "$R"
sleep 2
