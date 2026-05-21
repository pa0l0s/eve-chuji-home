#!/usr/bin/env bash
# One-off (re-runnable): optimise EVE wallpaper sources into static/backgrounds/bg-NN.jpg
# Requires ffmpeg. Run from anywhere:  ./scripts/optimize_backgrounds.sh
set -euo pipefail

SRC="/mnt/d/Download"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/static/backgrounds"

SOURCES=(
  "Desktop_1920x1080.jpg"
  "wUKReBj-eve-online-backgrounds.jpg"
  "skwEkVA-eve-online-wallpapers.jpg"
  "V7Q54ro-eve-online-wallpapers.jpg"
  "0Pn5YJb-eve-online-wallpapers.jpg"
  "UlyK49y-eve-online-wallpapers.jpg"
  "ZmDyMUQ-eve-online-wallpaper.jpg"
  "umfcZTa-eve-online-backgrounds.jpg"
  "5QcLo52-eve-online-backgrounds.jpg"
  "qbz4Txu-eve-online-wallpaper.jpg"
  "wp8755526-eve-online-wallpapers.jpg"
  "wp8755517-eve-online-wallpapers.jpg"
  "wp8755511-eve-online-wallpapers.jpg"
  "wp8755507-eve-online-wallpapers.jpg"
  "wp8755499-eve-online-wallpapers.jpg"
  "wp8755481-eve-online-wallpapers.jpg"
  "wp8755470-eve-online-wallpapers.jpg"
  "wp8755469-eve-online-wallpapers.jpg"
  "wp8755468-eve-online-wallpapers.jpg"
  "wp8755467-eve-online-wallpapers.jpg"
  "wp8755466-eve-online-wallpapers.jpg"
  "wp2006066-anathema-wallpapers.jpg"
  "wp8755464-eve-online-wallpapers.jpg"
  "wp8755463-eve-online-wallpapers.png"
  "wp8755462-eve-online-wallpapers.jpg"
  "wp8755461-eve-online-wallpapers.png"
  "wp8755459-eve-online-wallpapers.jpg"
  "wp8755457-eve-online-wallpapers.jpg"
  "wp8755456-eve-online-wallpapers.jpg"
  "wp8755455-eve-online-wallpapers.jpg"
  "wp8755454-eve-online-wallpapers.jpg"
  "wp8755453-eve-online-wallpapers.jpg"
  "wp8755449-eve-online-wallpapers.jpg"
  "wp8755446-eve-online-wallpapers.jpg"
  "xVtADSP-eve-online-backgrounds.jpg"
  "wp8755445-eve-online-wallpapers.png"
  "wp8755444-eve-online-wallpapers.jpg"
  "wp8755439-eve-online-wallpapers.jpg"
  "wp8755437-eve-online-wallpapers.jpg"
  "wp8755434-eve-online-wallpapers.jpg"
  "wp8755433-eve-online-wallpapers.jpg"
  "wp8755432-eve-online-wallpapers.jpg"
  "wp8755431-eve-online-wallpapers.jpg"
  "wp8755429-eve-online-wallpapers.jpg"
  "wp8755428-eve-online-wallpapers.jpg"
  "oVFi7Bf-eve-online-backgrounds.jpg"
  "wp8755423-eve-online-wallpapers.jpg"
  "wp8755412-eve-online-wallpapers.jpg"
  "wp8755411-eve-online-wallpapers.jpg"
  "wp8755410-eve-online-wallpapers.jpg"
  "6lJPB1t-eve-online-backgrounds.jpg"
  "wp8755409-eve-online-wallpapers.jpg"
  "background.jpg"
  "10-Screenshot_Copy_1920x1080.jpg"
  "12-Screenshot_Copy_1920x1080.jpg"
  "4-Screenshot_Copy_1920x1080.jpg"
  "14-Screenshot_Copy_1920x1080.jpg"
  "15-Screenshot_Copy_1920x1080.jpg"
  "16-Screenshot_Copy_1920x1080.jpg"
  "13-Screenshot_Copy_1920x1080.jpg"
)

# Sort for deterministic bg-NN numbering, independent of array order.
mapfile -t SORTED < <(printf '%s\n' "${SOURCES[@]}" | LC_ALL=C sort)

# Validate all sources exist before touching DEST.
for f in "${SORTED[@]}"; do
  if [[ ! -f "$SRC/$f" ]]; then
    echo "ERROR: missing source: $SRC/$f" >&2
    exit 1
  fi
done

mkdir -p "$DEST"
rm -f "$DEST"/bg-*

i=1
for f in "${SORTED[@]}"; do
  out=$(printf '%s/bg-%02d.jpg' "$DEST" "$i")
  ffmpeg -y -loglevel error -i "$SRC/$f" \
    -vf "scale='min(1920,iw)':-2" -map_metadata -1 -pix_fmt yuvj420p -q:v 5 "$out"
  echo "  $f -> bg-$(printf '%02d' "$i").jpg"
  i=$((i + 1))
done

echo "Done: $((i - 1)) images -> $DEST"
du -sh "$DEST"
