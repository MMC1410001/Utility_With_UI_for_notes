#!/bin/bash
# Start your real Chrome with a debugging port so notesgen can drive it.
#
# Cloudflare challenges automated browsers. Attaching to a Chrome you launched
# yourself sidesteps that entirely: it is your normal browser, with your normal
# session, and notesgen just borrows the tab.
#
#   ./attach-chrome.sh                     # then, in another terminal:
#   python3 -m notesgen fetch --attach -i "https://www.udemy.com/course/..."
set -euo pipefail
PORT="${1:-9222}"
PROFILE="${TMPDIR:-/tmp}/notesgen-chrome-profile"
mkdir -p "$PROFILE"
echo "Starting Chrome with remote debugging on port $PORT"
echo "Log in to Udemy in this window, then run the fetch with --attach $PORT"
exec "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check \
  "https://www.udemy.com/"
