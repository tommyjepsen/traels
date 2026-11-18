#!/usr/bin/env bash
# Builds build/Traels.app (release) with the Python backend bundled into Resources.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP="$ROOT/build/Traels.app"

swift build -c release
BIN="$(swift build -c release --show-bin-path)/Traels"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Traels"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>com.tommyjepsen.traels</string>
    <key>CFBundleName</key><string>Traels</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleDisplayName</key><string>Traels</string>
    <key>CFBundleExecutable</key><string>Traels</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>CFBundleDocumentTypes</key>
    <array>
        <dict>
            <key>CFBundleTypeName</key><string>Media and text</string>
            <key>CFBundleTypeRole</key><string>Viewer</string>
            <key>LSHandlerRank</key><string>Alternate</string>
            <key>LSItemContentTypes</key>
            <array>
                <string>public.image</string>
                <string>public.movie</string>
                <string>public.audio</string>
                <string>public.plain-text</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
PLIST

# Icon: regenerate with `swift scripts/make-icon.swift`.
cp "$ROOT/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

if [ -d "$ROOT/backend" ]; then
    mkdir -p "$APP/Contents/Resources/backend"
    rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' "$ROOT/backend/" "$APP/Contents/Resources/backend/"
else
    echo "warning: backend/ not found; the app will have no worker" >&2
fi

codesign --force --deep -s - "$APP"
echo "Built $APP"
