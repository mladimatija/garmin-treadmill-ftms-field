#!/usr/bin/env bash
# Builds one sideloadable .prg per watch: bin/FtmsTreadmill-<device id>.prg
#
#   ./build.sh                  every watch listed in manifest.xml
#   ./build.sh fenix7x fr965    only these watches
#
# Needs monkeyc (Connect IQ SDK) on the PATH, the watches' device files (SDK Manager) and a
# developer key.
#
#   DEVELOPER_KEY   signing key, default ~/developer_key.der
#   JOBS            parallel builds, default 4
set -euo pipefail
cd "$(dirname "$0")"

export DEVELOPER_KEY="${DEVELOPER_KEY:-$HOME/developer_key.der}"
jobs="${JOBS:-4}"

if ! command -v monkeyc > /dev/null; then
    echo "monkeyc not found: install the Connect IQ SDK and put its bin folder on the PATH" >&2
    exit 1
fi
if [[ ! -f "$DEVELOPER_KEY" ]]; then
    echo "Developer key not found: $DEVELOPER_KEY (set DEVELOPER_KEY)" >&2
    exit 1
fi

known=$(sed -n 's/.*<iq:product id="\([^"]*\)".*/\1/p' manifest.xml)
devices=()
if [[ $# -gt 0 ]]; then
    for d in "$@"; do
        if ! grep -qx "$d" <<< "$known"; then
            echo "Unknown device id '$d' (not in manifest.xml)" >&2
            exit 1
        fi
        devices+=("$d")
    done
else
    while IFS= read -r d; do
        devices+=("$d")
    done <<< "$known"
    rm -f bin/FtmsTreadmill-*.prg      # drop builds of watches no longer in the manifest
fi

# Each watch builds into its own folder (monkeyc writes intermediate files next to the .prg);
# only the .prg is copied to bin/.
build_one() {
    local d="$1"
    local out="bin/build/$d"
    mkdir -p "$out"
    if monkeyc -d "$d" -f monkey.jungle -o "$out/FtmsTreadmill.prg" -y "$DEVELOPER_KEY" -r \
        > "$out/build.log" 2>&1; then
        cp "$out/FtmsTreadmill.prg" "bin/FtmsTreadmill-$d.prg"
        echo "ok    $d"
    else
        echo "FAIL  $d  (see $out/build.log)"
    fi
}
export -f build_one

echo "Building ${#devices[@]} watch(es), $jobs at a time..."
# shellcheck disable=SC2016  # $1 is expanded by the child bash, not here
results=$(printf '%s\n' "${devices[@]}" | xargs -P "$jobs" -n 1 bash -c 'build_one "$1"' _)
sort <<< "$results"

failed=$(grep -c '^FAIL' <<< "$results" || true)
echo "$((${#devices[@]} - failed)) of ${#devices[@]} built into bin/"
[[ "$failed" -eq 0 ]]
