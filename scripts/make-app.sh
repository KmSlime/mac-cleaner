#!/bin/bash
# Build a Universal release (arm64 + x86_64) and package build/MacCleaner.app (ad-hoc signed).
#   ./scripts/make-app.sh            # Universal
#   ARCHS="arm64" ./scripts/make-app.sh   # one architecture (faster while developing)
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(sed -n 's/^__version__ = "\(.*\)".*/\1/p' mclib/__init__.py)"   # single source of the version
[ -n "$VERSION" ] || { echo "no __version__ in mclib/__init__.py" >&2; exit 1; }
ARCHS="${ARCHS:-arm64 x86_64}"
# Map the build machine's absolute path to "." so the shipped binary does not leak the username/directory.
# (debug info is removed by `strip -S` below, so -debug-prefix-map is not needed)
PREFIX_MAP=(-Xswiftc -file-prefix-map -Xswiftc "$PWD=.")

APP=build/MacCleaner.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/Web"

# Each --arch may write to the SAME output directory (newer SwiftPM) → copy the slice out right after its build.
SLICE_DIR="$(mktemp -d)"; trap 'rm -rf "$SLICE_DIR"' EXIT
SLICES=()
for arch in $ARCHS; do
  swift build -c release --arch "$arch" "${PREFIX_MAP[@]}"
  cp "$(swift build -c release --arch "$arch" "${PREFIX_MAP[@]}" --show-bin-path)/MacCleaner" "$SLICE_DIR/MacCleaner-$arch"
  SLICES+=("$SLICE_DIR/MacCleaner-$arch")
done
lipo -create "${SLICES[@]}" -output "$APP/Contents/MacOS/MacCleaner"
strip -S "$APP/Contents/MacOS/MacCleaner"   # drop debug stabs (they hold build-machine paths)

cp maccleaner.py "$APP/Contents/Resources/maccleaner.py"
rsync -a --exclude "__pycache__" mclib "$APP/Contents/Resources/"   # maccleaner.py imports mclib from next to itself
cp Web/board.html "$APP/Contents/Resources/Web/board.html"
cp LICENSE "$APP/Contents/Resources/LICENSE"
cp assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"   # redraw: swift scripts/make-icon.swift
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Mac Cleaner</string>
  <key>CFBundleDisplayName</key><string>Mac Cleaner</string>
  <key>CFBundleIdentifier</key><string>local.maccleaner</string>
  <key>CFBundleExecutable</key><string>MacCleaner</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>8</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$APP"
echo "Built $APP ($(lipo -archs "$APP/Contents/MacOS/MacCleaner"), v${VERSION})"
