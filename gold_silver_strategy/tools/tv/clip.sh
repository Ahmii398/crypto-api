#!/usr/bin/env bash
# Put a generated Pine script on the X clipboard so it can be pasted into TradingView's Pine Editor.
#   tools/tv/clip.sh strategy|indicator
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
case "${1:-}" in
  strategy) file="$root/pine/GSMS_Strategy.pine" ;;
  indicator) file="$root/pine/GSMS_Indicator.pine" ;;
  *) echo "usage: $0 strategy|indicator" >&2; exit 2 ;;
esac
DISPLAY="${DISPLAY:-:1}" xclip -selection clipboard -i < "$file"
echo "clipboard <- $file ($(wc -l < "$file") lines)"
