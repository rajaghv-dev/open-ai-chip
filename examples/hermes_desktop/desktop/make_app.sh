#!/usr/bin/env bash
# Build "Hermes Chip Agent.app" into build/desktop/. Add --install to also copy it to ~/Applications.
# Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
APP="$REPO/build/desktop/Hermes Chip Agent.app"
PY="$REPO/build/agent/venv/bin/python"
"$PY" -c "import webview" 2>/dev/null || { echo "pywebview missing: $REPO/build/agent/venv/bin/pip install pywebview" >&2; exit 1; }
rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$HERE/app.py" "$APP/Contents/Resources/app.py"
# icon: icon.png (committed, drawn by make_icon.py) -> icon.icns with sips + iconutil
IS="$(mktemp -d)/icon.iconset"; mkdir -p "$IS"
for s in 16 32 128 256 512; do
  sips -z $s $s "$HERE/icon.png" --out "$IS/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$HERE/icon.png" --out "$IS/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$IS" -o "$APP/Contents/Resources/icon.icns"
printf '%s\n' "$REPO" > "$APP/Contents/Resources/repo_path"
cat > "$APP/Contents/MacOS/hermes-chip-agent" <<'L'
#!/usr/bin/env bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # Finder gives apps a minimal PATH
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
REPO="$(cat "$RES/repo_path")"
mkdir -p "$REPO/build/webui/logs"
exec "$REPO/build/agent/venv/bin/python" "$RES/app.py" "$REPO" >>"$REPO/build/webui/logs/desktop_app.log" 2>&1
L
chmod +x "$APP/Contents/MacOS/hermes-chip-agent"
cat > "$APP/Contents/Info.plist" <<'P'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Hermes Chip Agent</string>
<key>CFBundleDisplayName</key><string>Hermes Chip Agent</string>
<key>CFBundleIdentifier</key><string>local.openaichip.hermeschipagent</string>
<key>CFBundleIconFile</key><string>icon</string>
<key>CFBundleExecutable</key><string>hermes-chip-agent</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>11.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
P
echo "built: $APP"
if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/Applications"; rm -rf "$HOME/Applications/Hermes Chip Agent.app"
  cp -R "$APP" "$HOME/Applications/"; echo "installed: ~/Applications/Hermes Chip Agent.app"
fi
