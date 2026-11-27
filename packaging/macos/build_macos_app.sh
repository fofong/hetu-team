#!/bin/bash
# 构建 Haneka Team.app（macOS WKWebView 壳）并安装到 ~/Applications
# 前置: packaging/macos/.venv 已 pip install pywebview；AppIcon.icns 已生成
set -euo pipefail
cd "$(dirname "$0")"
APP="$HOME/Applications/Haneka Team.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Haneka Team</string>
  <key>CFBundleDisplayName</key><string>Haneka Team</string>
  <key>CFBundleIdentifier</key><string>dev.hongyun.hanekateam</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleExecutable</key><string>HanekaTeam</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsArbitraryLoads</key><true/></dict>
</dict></plist>
PLIST

cat > "$APP/Contents/MacOS/HanekaTeam" <<'LAUNCH'
#!/bin/sh
DIR=$(cd "$(dirname "$0")" && pwd)
exec "$DIR/../Resources/venv/bin/python3" "$DIR/../Resources/gui.py" "$@"
LAUNCH
chmod +x "$APP/Contents/MacOS/HanekaTeam"

cp AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp gui.py "$APP/Contents/Resources/gui.py"
cp -R .venv "$APP/Contents/Resources/venv"
touch "$APP"
echo "built: $APP"
