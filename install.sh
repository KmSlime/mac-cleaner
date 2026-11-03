#!/bin/bash
# mac-cleaner — cài một lệnh trên máy Mac bất kỳ (macOS 13+, Apple Silicon hoặc Intel):
#   git clone <repo> && cd mac-cleaner && ./install.sh
#
# Làm:
#   1. Command Line Tools của Apple (Python 3 + Swift) — thiếu thì mở trình cài chính thức, cài xong chạy lại.
#   2. Mole (`mo`): Homebrew nếu có; không thì tải bản phát hành ghim sẵn, kiểm sha256, chép vào ~/.local.
#      KHÔNG chạy script cài của bên thứ ba.
#   3. App "Mac Cleaner" (dashboard) → ~/Applications (bản build từ source, Universal).
#   4. Lệnh `mac-cleaner` → ~/.local/bin (dùng chung report với app).
#   5. Có Claude Code (`claude`) → cài plugin mac-cleaner (skill mac-cleaner — gồm cả dọn sâu bằng Mole — + hook xin duyệt) từ chính repo này.
# Không dùng sudo. Chạy lại bao nhiêu lần cũng được (bỏ qua bước đã xong).
#   --no-app     bỏ build app       --no-mole    bỏ cài Mole       --no-plugin  bỏ plugin Claude Code
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
BIN="$HOME/.local/bin"
SHARE="$HOME/.local/share"
DATA="$HOME/Library/Application Support/MacCleaner"

MOLE_TAG="V1.68.0"
MOLE_SRC_SHA256="750b67f7428075e536d42788968502fc94e1c8e42bcc7d6da9f8b16968ebe106"   # archive/refs/tags/V1.68.0.tar.gz
MOLE_SUMS_SHA256="abbba9d21ac5abca0cf3057b5379ca9282dece0c44dea63e20b1c64d54f7cce0"  # SHA256SUMS của release

DO_APP=1; DO_MOLE=1; DO_PLUGIN=1
for a in "$@"; do
  case "$a" in
    --no-app) DO_APP=0 ;; --no-mole) DO_MOLE=0 ;; --no-plugin) DO_PLUGIN=0 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Không hiểu tham số: $a" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    ✓ %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
sha_ok() { [ "$(shasum -a 256 "$1" | awk '{print $1}')" = "$2" ]; }

# --- 0. Hệ điều hành
[ "$(uname -s)" = Darwin ] || { echo "mac-cleaner chỉ chạy trên macOS." >&2; exit 1; }
OSV="$(sw_vers -productVersion)"; [ "${OSV%%.*}" -ge 13 ] || { echo "Cần macOS 13 trở lên (máy đang là $OSV)." >&2; exit 1; }
ARCH="$(uname -m)"; case "$ARCH" in arm64) MARCH=arm64 ;; x86_64) MARCH=amd64 ;; *) echo "Kiến trúc lạ: $ARCH" >&2; exit 1 ;; esac
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

mkdir -p "$BIN" "$SHARE" "$DATA"

# --- 2. Mole
if [ "$DO_MOLE" = 1 ]; then
  say "Mole ($MOLE_TAG)"
  if command -v mo >/dev/null 2>&1; then
    ok "đã có: $(command -v mo)"
  elif command -v brew >/dev/null 2>&1; then
    brew install mole && ok "cài qua Homebrew"
  else
    TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
    U="https://github.com/tw93/mole/releases/download/$MOLE_TAG"
    curl -fsSL -o "$TMP/SHA256SUMS" "$U/SHA256SUMS"
    sha_ok "$TMP/SHA256SUMS" "$MOLE_SUMS_SHA256" || { warn "SHA256SUMS của Mole không khớp bản đã ghim — dừng."; exit 1; }
    curl -fsSL -o "$TMP/bin.tgz" "$U/binaries-darwin-$MARCH.tar.gz"
    want="$(awk -v f="binaries-darwin-$MARCH.tar.gz" '$2==f{print $1}' "$TMP/SHA256SUMS")"
    sha_ok "$TMP/bin.tgz" "$want" || { warn "binary Mole không khớp SHA256SUMS — dừng."; exit 1; }
    curl -fsSL -o "$TMP/src.tgz" "https://github.com/tw93/mole/archive/refs/tags/$MOLE_TAG.tar.gz"
    sha_ok "$TMP/src.tgz" "$MOLE_SRC_SHA256" || { warn "source Mole không khớp bản đã ghim — dừng."; exit 1; }
    D="$SHARE/mole"; rm -rf "$D.new"; mkdir -p "$D.new"
    tar -xzf "$TMP/src.tgz" -C "$TMP"
    S="$(find "$TMP" -maxdepth 1 -type d -name 'Mole-*' | head -1)"
    cp -R "$S/mole" "$S/bin" "$S/lib" "$D.new/"
    tar -xzf "$TMP/bin.tgz" -C "$TMP"
    cp "$TMP/analyze-darwin-$MARCH" "$D.new/bin/analyze-go"
    cp "$TMP/status-darwin-$MARCH"  "$D.new/bin/status-go"
    chmod +x "$D.new/mole" "$D.new/bin/"*
    xattr -dr com.apple.quarantine "$D.new" 2>/dev/null || true
    rm -rf "$D"; mv "$D.new" "$D"
    ln -sfn "$D/mole" "$BIN/mo"; ln -sfn "$D/mole" "$BIN/mole"
    ok "cài vào $D (đã kiểm sha256), lệnh: $BIN/mo"
  fi
fi

# --- 3. App Mac Cleaner (dashboard)
if [ "$DO_APP" = 1 ]; then
  say "App Mac Cleaner (Universal)"
  ./scripts/make-app.sh
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/MacCleaner.app"
  cp -R build/MacCleaner.app "$HOME/Applications/MacCleaner.app"
  ok "~/Applications/MacCleaner.app"
fi

# --- 4. Lệnh mac-cleaner
say "Lệnh mac-cleaner"
cat > "$BIN/mac-cleaner" <<EOF
#!/bin/bash
# Do mac-cleaner/install.sh tạo. Report dùng chung với app Mac Cleaner.
export MACCLEANER_DATA="\${MACCLEANER_DATA:-$DATA}"
exec python3 "$ROOT/maccleaner.py" "\$@"
EOF
chmod +x "$BIN/mac-cleaner"
ok "$BIN/mac-cleaner (report: $DATA)"

# --- 5. Plugin Claude Code (tuỳ có Claude)
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
say "Xong. Mở dashboard: open ~/Applications/MacCleaner.app · Quét: mac-cleaner scan"
