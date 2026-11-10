#!/bin/bash
# Update (or install) the Mac Cleaner app from the latest GitHub release. Works with every installed version, also the
# ones older than 1.10.0 that have no `self-update` command; needs only tools that ship with macOS. No sudo.
#   curl -fsSL https://raw.githubusercontent.com/KmSlime/mac-cleaner/develop/update.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/KmSlime/mac-cleaner/develop/update.sh | bash -s -- /path/to/MacCleaner.app
# Steps: find the app (argument → ~/Applications → /Applications; none → install into ~/Applications) → read the latest
# release tag → download the zip + .sha256 → check SHA-256, bundle id, version and code signature → move the old app to
# the Trash → put the new one in its place. Nothing is touched before every check has passed.
# MACCLEANER_RELEASES replaces the releases URL and MACCLEANER_TAG pins a tag (the tests use both).
set -euo pipefail

REL="${MACCLEANER_RELEASES:-https://github.com/KmSlime/mac-cleaner/releases}"
BUNDLE_ID="local.maccleaner"   # the same since the first release

die() { echo "✗ $*" >&2; exit 1; }
plist() { /usr/libexec/PlistBuddy -c "Print :$2" "$1/Contents/Info.plist" 2>/dev/null || true; }

schedules_hint() {   # Mac Cleaner's own LaunchAgents (label local.maccleaner.<name>), e.g. an older install's: the app lists them in Settings
  local f label names=""
  for f in "$HOME/Library/LaunchAgents"/*.plist; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    label="$(/usr/libexec/PlistBuddy -c "Print :Label" "$f" 2>/dev/null || true)"
    case "$label" in "$BUNDLE_ID".?*) names="${names:+$names, }$label" ;; esac
  done
  [ -n "$names" ] || return 0
  echo "  Máy đang có lịch của Mac Cleaner: ${names}."
  echo "  Mở app → Cài đặt → \"Lịch đang có trên máy\" để sửa (cho lịch chạy script của bản mới) hoặc xoá."
}

main() {   # everything runs inside a function, so a download cut off half-way (curl | bash) runs nothing
  local app="${1:-}" c old="" tag url ver name tmp want got new trash
  if [ -z "$app" ]; then
    app="$HOME/Applications/MacCleaner.app"   # where a first install goes
    for c in "$HOME/Applications/MacCleaner.app" "/Applications/MacCleaner.app"; do
      if [ -d "$c" ]; then app="$c"; break; fi
    done
  fi
  case "$app" in /*.app) ;; *) die "cần đường dẫn đầy đủ tới MacCleaner.app, không phải: $app" ;; esac
  if [ -e "$app" ]; then
    [ "$(plist "$app" CFBundleIdentifier)" = "$BUNDLE_ID" ] || die "$app không phải Mac Cleaner — không đụng tới"
    old="$(plist "$app" CFBundleShortVersionString)"
  fi

  tag="${MACCLEANER_TAG:-}"
  if [ -z "$tag" ]; then   # the "latest" page redirects to …/releases/tag/vX.Y.Z: no API, no rate limit
    url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REL/latest")" || die "không kết nối được GitHub — kiểm tra mạng rồi chạy lại"
    tag="${url##*/}"
  fi
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "không đọc được số phiên bản mới nhất (nhận: $tag)"
  ver="${tag#v}"
  if [ "$old" = "$ver" ]; then echo "✓ Mac Cleaner đang ở bản mới nhất ($ver) — $app"; return 0; fi
  if [ -n "$old" ] && [ "$(printf '%s\n%s\n' "$old" "$ver" | sort -V | tail -1)" = "$old" ]; then
    echo "✓ Bản đang cài ($old) mới hơn bản phát hành ($ver) — không làm gì."; return 0
  fi

  name="MacCleaner-$ver-macos-universal.zip"
  tmp="$(mktemp -d)"; trap "rm -rf '$tmp'" EXIT
  echo "▸ Tải ${name}…"   # braces: bash 3.2 reads a non-ASCII byte as part of the name
  curl -fsSL -o "$tmp/$name" "$REL/download/$tag/$name" || die "không tải được $name từ bản phát hành $tag"
  curl -fsSL -o "$tmp/$name.sha256" "$REL/download/$tag/$name.sha256" || die "bản phát hành $tag thiếu file .sha256"
  want="$(awk '{print tolower($1); exit}' "$tmp/$name.sha256")"
  got="$(shasum -a 256 "$tmp/$name" | awk '{print $1}')"
  [ -n "$want" ] && [ "$want" = "$got" ] || die "SHA-256 của gói tải về không khớp — huỷ, app đang cài còn nguyên"

  echo "▸ Kiểm gói (SHA-256 khớp), bundle id, phiên bản, chữ ký…"
  ditto -x -k "$tmp/$name" "$tmp/x" || die "không giải nén được $name"
  new="$tmp/x/MacCleaner.app"
  [ -d "$new" ] || die "gói không chứa MacCleaner.app"
  [ "$(plist "$new" CFBundleIdentifier)" = "$BUNDLE_ID" ] || die "gói có bundle id lạ — huỷ"
  [ "$(plist "$new" CFBundleShortVersionString)" = "$ver" ] || die "gói không phải bản $ver — huỷ"
  codesign --verify --deep --strict "$new" 2>/dev/null || die "chữ ký mã của gói không hợp lệ — huỷ"

  mkdir -p "$(dirname "$app")" || die "không tạo được $(dirname "$app")"
  if [ -e "$app" ]; then
    trash="$HOME/.Trash/MacCleaner ${old:-cu} ($(date +%Y%m%d-%H%M%S)).app"
    mkdir -p "$HOME/.Trash"
    mv "$app" "$trash" || die "không chuyển được app cũ vào Thùng rác (thiếu quyền ghi ở $(dirname "$app")?) — chưa đổi gì"
    echo "▸ App cũ${old:+ ($old)} đã vào Thùng rác."
  fi
  if ! ditto "$new" "$app"; then
    rm -rf "$app"   # only the partial copy just written; the old app comes back from the Trash below
    if [ -n "${trash:-}" ]; then mv "$trash" "$app"; fi
    die "không đặt được app mới vào $app — đã trả app cũ về chỗ cũ"
  fi

  echo "✓ Mac Cleaner ${old:+$old → }$ver — $app"
  if pgrep -f "$app/Contents/MacOS/MacCleaner" >/dev/null 2>&1; then
    echo "  App đang mở vẫn là bản cũ: thoát (⌘Q) rồi mở lại để chạy bản $ver."
  else
    echo "  Mở app: open \"$app\""
  fi
  schedules_hint
}

main "$@"
