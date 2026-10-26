#!/bin/zsh
set -euo pipefail
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

jarvis_tmp_dir=$(mktemp -d)
trap 'rm -rf "$jarvis_tmp_dir"' EXIT

/usr/bin/say -v Daniel -o "$jarvis_tmp_dir/fixture.aiff" 'Jarvis test'
".local/Jarvis Desktop.app/Contents/MacOS/JarvisDesktop" --check-speech-fixture "$jarvis_tmp_dir/fixture.aiff"
