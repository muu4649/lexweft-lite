#!/usr/bin/env bash
# Mac アプリ「LeXWeft Lite.app」を、この Mac の上で作る (install.sh から呼ぶ)
#   使い方: build_app.sh <lexweft コマンドの絶対パス> <作る .app のパス>
# Swift のコンパイラ (Xcode の Command Line Tools) が要る。手元で作るので、Apple の公証は要らない。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CMD="$1"
OUT="$2"
[ -x "$CMD" ] || { echo "lexweft が見つかりません: $CMD" >&2; exit 1; }
VER="$("$CMD" --version)"
# 元のコードと起動するコマンドが前と同じなら、作り直さない。
# 作り直すと署名が変わり、macOS が別のアプリとみなして「書類フォルダへのアクセス」などの許可をまた求めるため
STAMP="$(cat "$HERE/App.swift" "$HERE/icon.icns" | shasum -a 256 | cut -d' ' -f1)-$(printf %s "$CMD" | shasum -a 256 | cut -c1-16)"
if [ -f "$OUT/Contents/Resources/build-stamp" ] && [ "$(cat "$OUT/Contents/Resources/build-stamp")" = "$STAMP" ]; then
  echo "Mac アプリは前のままで使えます: $OUT"
  exit 0
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

swiftc -O -target "$(uname -m)-apple-macos12.0" -o "$TMP/LeXWeftLite" "$HERE/App.swift"

rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
cp "$TMP/LeXWeftLite" "$OUT/Contents/MacOS/LeXWeftLite"
cp "$HERE/icon.icns" "$OUT/Contents/Resources/icon.icns"
printf %s "$STAMP" > "$OUT/Contents/Resources/build-stamp"
PLIST="$OUT/Contents/Info.plist"
cat > "$PLIST" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.lexivent.lexweft-lite</string>
  <key>CFBundleName</key><string>LeXWeft Lite</string>
  <key>CFBundleDisplayName</key><string>LeXWeft Lite</string>
  <key>CFBundleExecutable</key><string>LeXWeftLite</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0</string>
  <key>CFBundleVersion</key><string>0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>LWLiteCommand</key><string></string>
</dict>
</plist>
XML
plutil -replace CFBundleShortVersionString -string "$VER" "$PLIST"
plutil -replace CFBundleVersion -string "$VER" "$PLIST"
plutil -replace LWLiteCommand -string "$CMD" "$PLIST"
plutil -lint -s "$PLIST"
codesign --force --sign - "$OUT" >/dev/null 2>&1 || true
echo "作りました: $OUT ($VER の時点で作成。中身の版が上がっても、アプリの殻は作り直さない)"
