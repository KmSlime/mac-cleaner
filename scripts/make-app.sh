#!/bin/bash
# Build release Universal (arm64 + x86_64) và đóng gói build/MacCleaner.app (ký ad-hoc).
#   ./scripts/make-app.sh            # Universal
#   ARCHS="arm64" ./scripts/make-app.sh   # một kiến trúc (nhanh hơn khi phát triển)
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="1.5.0"
ARCHS="${ARCHS:-arm64 x86_64}"
# Đổi đường dẫn tuyệt đối của máy build thành "." — binary phát hành không lộ username/thư mục.
# (debug info bị `strip -S` bỏ hẳn bên dưới nên không cần -debug-prefix-map)
PREFIX_MAP=(-Xswiftc -file-prefix-map -Xswiftc "$PWD=.")

APP=build/MacCleaner.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/Web"

# Mỗi --arch có thể ghi vào CÙNG thư mục output (SwiftPM mới) → chép slice ra ngay sau khi build.
SLICE_DIR="$(mktemp -d)"; trap 'rm -rf "$SLICE_DIR"' EXIT
SLICES=()
for arch in $ARCHS; do
  swift build -c release --arch "$arch" "${PREFIX_MAP[@]}"
  cp "$(swift build -c release --arch "$arch" "${PREFIX_MAP[@]}" --show-bin-path)/MacCleaner" "$SLICE_DIR/MacCleaner-$arch"
  SLICES+=("$SLICE_DIR/MacCleaner-$arch")
done
lipo -create "${SLICES[@]}" -output "$APP/Contents/MacOS/MacCleaner"
strip -S "$APP/Contents/MacOS/MacCleaner"   # bỏ debug stab (chứa đường dẫn máy build)

cp maccleaner.py "$APP/Contents/Resources/maccleaner.py"
cp Web/board.html "$APP/Contents/Resources/Web/board.html"
cp LICENSE "$APP/Contents/Resources/LICENSE"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Mac Cleaner</string>
  <key>CFBundleDisplayName</key><string>Mac Cleaner</string>
  <key>CFBundleIdentifier</key><string>local.maccleaner</string>
  <key>CFBundleExecutable</key><string>MacCleaner</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>7</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$APP"
echo "Built $APP ($(lipo -archs "$APP/Contents/MacOS/MacCleaner"), v${VERSION})"
