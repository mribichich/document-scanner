#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <folder> [endpoint_url]" >&2
  echo "  folder        Directory of .png/.jpg/.jpeg images to POST to /detect" >&2
  echo "  endpoint_url  Optional. Defaults to \`terraform -chdir=infra output -raw detect_endpoint\`" >&2
  exit 1
}

[ $# -ge 1 ] || usage
FOLDER="$1"
[ -d "$FOLDER" ] || { echo "Not a directory: $FOLDER" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENDPOINT="${2:-$(terraform -chdir="$SCRIPT_DIR/../infra" output -raw detect_endpoint)}"

# Infer which algorithm this endpoint runs from its route path, so results
# from /detect (CV) and /detect-textract (Textract) never collide on disk.
case "$ENDPOINT" in
  */detect-textract) ALGO="textract" ;;
  */detect) ALGO="cv" ;;
  *)
    echo "Warning: couldn't infer algo from endpoint URL path ($ENDPOINT); using 'unknown'" >&2
    ALGO="unknown"
    ;;
esac

shopt -s nullglob nocaseglob
FILES=("$FOLDER"/*.png "$FOLDER"/*.jpg "$FOLDER"/*.jpeg)
shopt -u nocaseglob

[ ${#FILES[@]} -gt 0 ] || { echo "No .png/.jpg/.jpeg files found in $FOLDER" >&2; exit 1; }

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS_DIR="$FOLDER/results/$ALGO/$TIMESTAMP"
mkdir -p "$RESULTS_DIR"

echo "Endpoint: $ENDPOINT"
echo "Algo: $ALGO"
echo "Files: ${#FILES[@]}"
echo

for f in "${FILES[@]}"; do
  base="$(basename "$f")"
  name="${base%.*}"
  out="$RESULTS_DIR/$name.json"

  status=$(curl -s -o "$out" -w "%{http_code}" -X POST "$ENDPOINT" -F "file=@$f")

  if [ "$status" != "200" ]; then
    echo "$base: HTTP $status (see $out)"
    continue
  fi

  python3 -c "
import json
d = json.load(open('$out'))
boxes = d.get('boxes', [])
checked = sum(1 for b in boxes if b['is_checked'])
print(f'$base: HTTP $status  boxes={len(boxes)}  checked={checked}  unchecked={len(boxes) - checked}')
"
done

echo
echo "Raw responses saved to $RESULTS_DIR/"
