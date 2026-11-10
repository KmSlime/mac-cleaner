#!/bin/bash
# mac-cleaner — cài một lệnh trên máy Mac bất kỳ (macOS 13+, Apple Silicon hoặc Intel):
#   git clone <repo> && cd mac-cleaner && ./install.sh
#
# Làm:
#   1. Command Line Tools của Apple (Python 3 + Swift) — thiếu thì mở trình cài chính thức, cài xong chạy lại.
#   2. App "Mac Cleaner" (dashboard) → ~/Applications (bản build từ source, Universal).
#   3. Lệnh `mac-cleaner` → ~/.local/bin (dùng chung report với app).
#   4. Có Claude Code (`claude`) → cài plugin mac-cleaner (skill mac-cleaner + hook xin duyệt) từ chính repo này.
# Không dùng sudo, không tải gì từ bên thứ ba (không còn phụ thuộc Mole). Chạy lại bao nhiêu lần cũng được.
#   --no-app     bỏ build app       --no-plugin  bỏ plugin Claude Code
#   --no-mole    (cũ, không còn tác dụng — giữ để lệnh cài cũ không lỗi)
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
BIN="$HOME/.local/bin"
DATA="$HOME/Library/Application Support/MacCleaner"

DO_APP=1; DO_PLUGIN=1; OLD_MOLE_FLAG=0
for a in "$@"; do
  case "$a" in
    --no-app) DO_APP=0 ;; --no-mole) OLD_MOLE_FLAG=1 ;; --no-plugin) DO_PLUGIN=0 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Không hiểu tham số: $a" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    ✓ %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
[ "$OLD_MOLE_FLAG" = 1 ] && warn "--no-mole không còn tác dụng: install.sh không cài Mole nữa (bỏ cờ này đi cũng được)"

# --- 0. Hệ điều hành
[ "$(uname -s)" = Darwin ] || { echo "mac-cleaner chỉ chạy trên macOS." >&2; exit 1; }
OSV="$(sw_vers -productVersion)"; [ "${OSV%%.*}" -ge 13 ] || { echo "Cần macOS 13 trở lên (máy đang là $OSV)." >&2; exit 1; }
ARCH="$(uname -m)"; case "$ARCH" in arm64|x86_64) ;; *) echo "Kiến trúc lạ: $ARCH" >&2; exit 1 ;; esac
say "macOS $OSV · $ARCH"

# --- 1. Command Line Tools (Python 3 + Swift)
say "Command Line Tools (Python 3, Swift)"
if xcode-select -p >/dev/null 2>&1; then
  ok "có sẵn: $(xcode-select -p)"
else
  warn "Chưa có. Mở trình cài chính thức của Apple — bấm Cài đặt, chờ xong rồi chạy lại ./install.sh"
  xcode-select --install 2>/dev/null || true
  exit 1
fi
command -v python3 >/dev/null && python3 -c 'import sys; assert sys.version_info >= (3, 9)' \
  && ok "$(python3 --version)" || { warn "python3 chưa chạy được — chạy lại sau khi cài xong Command Line Tools"; exit 1; }

mkdir -p "$BIN" "$DATA"

# --- 2. App Mac Cleaner (dashboard)
if [ "$DO_APP" = 1 ]; then
  say "App Mac Cleaner (Universal)"
  ./scripts/make-app.sh
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/MacCleaner.app"
  cp -R build/MacCleaner.app "$HOME/Applications/MacCleaner.app"
  ok "~/Applications/MacCleaner.app"
fi

# --- 3. Lệnh mac-cleaner
say "Lệnh mac-cleaner"
cat > "$BIN/mac-cleaner" <<EOF
#!/bin/bash
# Do mac-cleaner/install.sh tạo. Report dùng chung với app Mac Cleaner.
export MACCLEANER_DATA="\${MACCLEANER_DATA:-$DATA}"
exec python3 "$ROOT/maccleaner.py" "\$@"
EOF
chmod +x "$BIN/mac-cleaner"
ok "$BIN/mac-cleaner (report: $DATA)"

# --- 3b. Schedules that already run mac-cleaner on this machine (an older install's, or another tool's)
if scheds="$("$BIN/mac-cleaner" self-update schedule list 2>/dev/null)" && [ "$(printf '%s\n' "$scheds" | wc -l)" -gt 1 ]; then
  say "Lịch của Mac Cleaner đang có trên máy"
  printf '%s\n' "$scheds" | sed '1d'
  warn "Xem, sửa (cho lịch chạy script của bản vừa cài) hoặc xoá: mở app → Cài đặt → \"Lịch đang có trên máy\", hoặc: mac-cleaner self-update schedule edit|remove <nhãn>"
fi

# --- 4. Plugin Claude Code (tuỳ có Claude)
if [ "$DO_PLUGIN" = 1 ]; then
  say "Plugin Claude Code"
  if command -v claude >/dev/null 2>&1; then
    claude plugin marketplace add "$ROOT" >/dev/null 2>&1 || true
    if claude plugin install mac-cleaner@mac-cleaner >/dev/null 2>&1 || claude plugin list 2>/dev/null | grep -q 'mac-cleaner@mac-cleaner'; then
      ok "skill mac-cleaner + hook xin duyệt (mở phiên Claude Code mới để nạp)"
    else
      warn "chưa cài được plugin — thử: claude plugin marketplace add \"$ROOT\" && claude plugin install mac-cleaner@mac-cleaner"
    fi
  else
    ok "không có Claude Code — bỏ qua (app và CLI vẫn dùng bình thường)"
  fi
fi

case ":$PATH:" in *":$BIN:"*) ;; *) warn "Thêm $BIN vào PATH: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && exec zsh" ;; esac
say "Xong — $("$BIN/mac-cleaner" --version 2>/dev/null || echo mac-cleaner). Mở dashboard: open ~/Applications/MacCleaner.app · Quét: mac-cleaner scan"
