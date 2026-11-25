#!/bin/sh
# Mutations of the installed system for tests/niri_search_installed_smoke.py: each check must fail when the
# behaviour it covers is switched off, and the unmutated copy must pass. Run in the guest from the checkout's root,
# as the guest's user. It takes the screen lock, stops the installed shell and starts it again at the end.
#
# A mutant shell is a copy of /etc/flow with one line changed, started as the flow-shell unit starts the installed
# one (the session's PATH, FLOW_APP_SCOPES=1). A mutant flow-search is a wrapper first on that shell's PATH that
# answers one request itself and runs the installed flow-search for the rest. The test runs with the installed PATH.
set -u
eval "$(systemctl --user show-environment | grep -E '^(NIRI_SOCKET|WAYLAND_DISPLAY|XDG_RUNTIME_DIR|PATH)=' | sed 's/^/export /')"
exec 9>/tmp/floe-screen.lock
flock -w 1800 9 || exit 9
work=$(mktemp -d /tmp/f008-mutants-XXXXXX)
shell_pid=
INSTALLED=$(command -v flow-search)
stop_mutant() { if [ -n "$shell_pid" ]; then kill "$shell_pid" 2>/dev/null; wait "$shell_pid" 2>/dev/null; fi; shell_pid=; }
finish() { stop_mutant; systemctl --user start flow-shell; rm -rf "$work"; }
trap finish EXIT
trap 'exit 130' INT TERM
systemctl --user stop flow-shell

# start_shell OLD NEW SEARCH: a copy of the installed shell with OLD replaced by NEW (both empty: unchanged), and a
# flow-search wrapper that runs SEARCH first (a shell snippet that may exit), started the way the unit starts it.
start_shell() {
  rm -rf "$work/shell" "$work/bin"; cp -rL /etc/flow "$work/shell"; chmod -R u+w "$work/shell"; mkdir "$work/bin"
  python3 - "$work/shell/shell.qml" "$1" "$2" <<'PY'
import sys
path, old, new = sys.argv[1:]
text = open(path).read()
if old:
    assert text.count(old) == 1, f"{text.count(old)} matches for {old!r}"
    text = text.replace(old, new)
open(path, "w").write(text)
PY
  printf '#!/bin/sh\n%s\nexec %s "$@"\n' "$3" "$INSTALLED" > "$work/bin/flow-search"; chmod +x "$work/bin/flow-search"
  FLOW_APP_SCOPES="${MUT_SCOPES:-1}" PATH="$work/bin:$PATH" quickshell -p "$work/shell/shell.qml" >/dev/null 2>&1 &
  shell_pid=$!
  for _ in $(seq 60); do
    quickshell ipc -p "$work/shell/shell.qml" call desk search_state 2>/dev/null | grep -q closed && { sleep 5; return 0; }  # let its startup refresh settle
    sleep 0.5
  done
  echo "the mutant shell did not start"; exit 1
}
failed=0
# mutant DESCRIPTION CHECK OLD NEW SEARCH EXPECTED: the check must fail with a message holding EXPECTED, so a
# flake (a keymap race, a slow app) is not counted as a kill.
mutant() {
  [ -z "${ONLY:-}" ] || [ "$ONLY" = "$2" ] || return 0   # ONLY=check runs the mutants of one check
  stop_mutant
  start_shell "$3" "$4" "$5"
  FLOW_SHELL="$work/shell/shell.qml" timeout 300 python3 tests/niri_search_installed_smoke.py --only "$2" > "$work/out" 2>&1; rc=$?
  stop_mutant
  if [ $rc = 0 ]; then echo "SURVIVED  [$2] $1"; failed=1
  elif ! grep -qF -- "$6" "$work/out"; then echo "WRONG FAILURE (expected '$6')  [$2] $1: $(tail -1 "$work/out" | cut -c1-200)"; failed=1
  else echo "killed    [$2] $1: $(tail -1 "$work/out" | cut -c1-160)"; fi
}

start_shell '' '' ''
if [ -n "${ONLY:-}" ]; then echo "(control skipped: ONLY=$ONLY)"; elif FLOW_SHELL="$work/shell/shell.qml" timeout 600 python3 tests/niri_search_installed_smoke.py > "$work/out" 2>&1; then
  echo "control passed: every check, against an unmutated copy of the installed shell"
else
  echo "CONTROL FAILED"; cat "$work/out"; exit 1
fi
OPEN_APP='if [ "${1:-}" = --open ] && [ "${2:-}" = app ]; then exit 0; fi'
OPEN_FILE='if [ "${1:-}" = --open ] && [ "${2:-}" = file ]; then exit 0; fi'
NO_RECENT='if [ "${1:-}" = --json ]; then '"$INSTALLED"' --json | python3 -c "import json,sys; print(json.dumps([r for r in json.load(sys.stdin) if not r[\"detail\"].startswith(\"Recent file\")]))"; exit 0; fi'
FOCUS_PIN='else if (result.kind === "pin") spaceAction("focus", result.value)'
mutant "flow-search --open app does nothing" apps '' '' "$OPEN_APP" "a new window (foot)"
mutant "a click on a row does nothing" click 'onClicked: root.runSearchResult(resultRow.modelData)' 'onClicked: {}' '' "search to close after the choice"
mutant "choosing a pin does not focus it" spaces "$FOCUS_PIN" 'else if (result.kind === "pin") {}' '' "focus on the pinned window"
mutant "choosing a window does not focus it" spaces 'else if (result.kind === "window") spaceAction("focus-window", result.value)' 'else if (result.kind === "window") {}' '' "focus on the unpinned window"
# A pin whose app is closed says "Pinned space · Open" (it offers to open it); an open one says "Pinned space".
mutant "a pin whose app is closed is not launched" closed-pin "$FOCUS_PIN" 'else if (result.kind === "pin" && result.detail.indexOf("Open") < 0) spaceAction("focus", result.value)' '' "a new window (flow-f008"
mutant "Flow Settings does not expand Settings" settings '                if (!deskOpen) toggleDesk()
                settingsOpen = true' '                if (!deskOpen) toggleDesk()' '' "Settings expanded"
mutant "Quick Settings does not open" quick 'if (result.value === "quick_open") { if (!quickSettingsOpen) toggleQuickSettings() }' 'if (result.value === "quick_open") { }' '' "Quick Settings to open"
mutant "flow-search --open file does nothing" recent '' '' "$OPEN_FILE" "a new window (org.xfce.mousepad)"
mutant "flow-search lists no recent files" recent '' '' "$NO_RECENT" "results for"
MUT_SCOPES=0 mutant "the shell starts apps in no scope of their own (no FLOW_APP_SCOPES)" apps '' '' '' "runs in"
exit $failed
