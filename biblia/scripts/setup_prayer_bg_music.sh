#!/usr/bin/env bash
# Скачать фоновые треки для голосовых молитв (YouTube → mp3).
#
#   ./scripts/setup_prayer_bg_music.sh
#   ./scripts/setup_prayer_bg_music.sh --out-dir /home/appuser/biblia/assets/prayer_bg_music
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${PRAYER_BG_MUSIC_DIR:-/home/appuser/biblia/assets/prayer_bg_music}"
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    *)
      echo "usage: $0 [--out-dir PATH] [--force]" >&2
      exit 1
      ;;
  esac
done

declare -A TRACKS=(
  [prayer_bg_01]="https://www.youtube.com/watch?v=Py3w3iYuQg0"
  [prayer_bg_02]="https://www.youtube.com/watch?v=DVPVUJNeLao"
  [prayer_bg_03]="https://www.youtube.com/watch?v=A03sE1nH0Cg"
)

pick_python() {
  for cand in \
    "/home/appuser/biblia/venv/bin/python" \
    "$ROOT/venv/bin/python" \
    "$(command -v python3)"
  do
    [[ -n "$cand" && -x "$cand" ]] || continue
    echo "$cand"
    return 0
  done
  return 1
}

PY="$(pick_python)" || { echo "python not found" >&2; exit 1; }

if ! "$PY" -c "import yt_dlp" 2>/dev/null; then
  echo "==> installing yt-dlp into venv"
  "${PY%python}pip" install -q yt-dlp
fi

YTDLP=("$PY" -m yt_dlp)
YTDLP+=(--extractor-args "youtube:player_client=web,default")
mkdir -p "$OUT_DIR"

echo "==> output: $OUT_DIR"
for name in prayer_bg_01 prayer_bg_02 prayer_bg_03; do
  url="${TRACKS[$name]}"
  out="$OUT_DIR/${name}.mp3"
  if [[ -f "$out" && "$FORCE" -eq 0 ]]; then
    sz=$(stat -c%s "$out" 2>/dev/null || echo 0)
    if (( sz > 10000 )); then
      echo "  skip $name (already exists, ${sz} bytes)"
      continue
    fi
  fi
  echo "==> download $name"
  "${YTDLP[@]}" -x --audio-format mp3 --audio-quality 0 \
    -o "${OUT_DIR}/${name}.%(ext)s" "$url"
done

echo ""
echo "==> files:"
ls -lh "$OUT_DIR"/prayer_bg_*.mp3
echo ""
echo "OK — PRAYER_BG_MUSIC_DIR=$OUT_DIR"
