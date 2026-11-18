#!/bin/bash
# CI: update an install of an older Tabbie to this commit through the real updater, then roll back.
#   tests/ci/update-from-old.sh <this repo> <how many commits back> <scratch dir>
# Twice: with the OLD version's updater (what a teammate's Mac runs), then with this version's updater
# working on the old checkout (preflight, switch, rollback). No services are touched (TABBIE_NO_SERVICES).
set -euo pipefail
NEW="$1"; BACK="${2:-5}"; WORK="$3"
export TABBIE_NO_SERVICES=1
git -C "$NEW" branch -f ci-new HEAD
OLD="$(git -C "$NEW" rev-parse "HEAD~$BACK")"
run() {  # $1 = folder to update, $2 = folder whose updater code to use
  COMMAND_CENTRE_HOME="$1" /usr/bin/env python3 -I - "$NEW" "$2" <<'PY'
import importlib, importlib.util, os, sys
new, code = sys.argv[1], sys.argv[2]
pkg = os.path.join(code, "plugin", "command-centre")
spec = importlib.util.spec_from_file_location("cc", os.path.join(pkg, "__init__.py"), submodule_search_locations=[pkg])
sys.modules["cc"] = importlib.util.module_from_spec(spec)
u = importlib.import_module("cc.updates")
u.ROOT = __import__("pathlib").Path(os.environ["COMMAND_CENTRE_HOME"])
u.STATE = u.ROOT / "state" / "updates.json"
u._url = lambda s: new
u.settings = lambda: {"repo": "x/y", "branch": "ci-new", "check": True}
u.check = lambda force=False: {"available": True}
before = u._git("rev-parse", "HEAD")
r = u.apply()
print("apply:", {k: r.get(k) for k in ("ok", "error", "from", "to", "needs", "warnings")})
assert r["ok"], r
if hasattr(u, "rollback"):
    u.restart_detached_wait = lambda max_wait=0: {"ok": True}
    rb = u.rollback("ci")
    print("rollback:", rb)
    assert rb["ok"] and u._git("rev-parse", "HEAD") == before, rb
PY
}
for mode in old new; do
  D="$WORK/update-$mode"
  git clone -q "$NEW" "$D"
  git -C "$D" checkout -q -B ci-new "$OLD"
  if [ "$mode" = old ]; then run "$D" "$D"; else run "$D" "$NEW"; fi
  echo "update with the $mode updater: ok"
done
