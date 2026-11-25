#!/bin/sh
# Runs tests/niri_snapshot_import_smoke.py against this checkout's session/, inside a Niri session where Flow
# is installed: flow-spaces and flow-recovery run this checkout from wrappers in a temporary directory, with the
# installed wrappers' Python and gio. Hold the guest's screen lock while it runs.
set -eu
checkout=$(cd "$(dirname "$0")/.." && pwd)
installed=$(command -v flow-recovery) || { echo "flow-recovery not found: install Flow" >&2; exit 1; }
for tool in foot niri unshare strace; do
    command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
python=$(sed -n 's/^exec \([^ ]*python3\) .*/\1/p' "$installed")
glib=$(grep -o '/nix/store/[^/:"]*-glib-[^/:"]*/bin' "$installed" | head -n 1 || true)
[ -n "$python" ] && [ -n "$glib" ] || { echo "$installed names no Python or glib: its shape has changed" >&2; exit 1; }
bin=$(mktemp -d "${TMPDIR:-/tmp}/flow-snapshot-bin.XXXXXX")
trap 'rm -rf "$bin"' EXIT
for pair in spaces:spaces recovery:restore; do
    name=${pair%%:*} module=${pair#*:}
    cat > "$bin/flow-$name" <<WRAP
#!/bin/sh
export PYTHONPATH="$checkout/session" PATH="$glib:\$PATH"
export XDG_DATA_DIRS="\${XDG_DATA_DIRS:+\$XDG_DATA_DIRS:}/run/current-system/sw/share"
exec "$python" -S -P -m $module "\$@"
WRAP
    chmod +x "$bin/flow-$name"
done
PATH="$bin:$PATH" python3 "$checkout/tests/niri_snapshot_import_smoke.py"
