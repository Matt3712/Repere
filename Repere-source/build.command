#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
SOURCE_DIR="$PWD"
APP_DIR="${SOURCE_DIR:h}/Repere.app"
RESOURCE_DIR="$APP_DIR/Contents/Resources"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp/}repere-build.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT
PYTHON_BIN="${REPERE_PYTHON:-python3}"
mkdir -p "$BUILD_DIR/swift-cache" "$APP_DIR/Contents/MacOS" "$RESOURCE_DIR"

# This personal Mac build uses the selected locally installed Python runtime.
# Its packages are isolated inside the application bundle.
if [[ ! -x "$RESOURCE_DIR/runtime/bin/python3" ]]; then
  "$PYTHON_BIN" -m venv --copies "$RESOURCE_DIR/runtime"
fi
# Earlier builds used a venv symlink to the system interpreter, which strict
# bundle validation rejects. Copy external targets; keep relative aliases.
"$PYTHON_BIN" - "$RESOURCE_DIR/runtime/bin" <<'PY'
from pathlib import Path
import shutil
import sys

bin_dir = Path(sys.argv[1]).resolve()
entries = sorted(bin_dir.iterdir(),
                 key=lambda path: path.is_symlink() and path.readlink().is_absolute(),
                 reverse=True)
for entry in entries:
    if entry.is_symlink() and not entry.resolve().is_relative_to(bin_dir):
        target = entry.resolve(strict=True)
        temporary = entry.with_name(entry.name + '.repere-copy')
        shutil.copy2(target, temporary)
        temporary.replace(entry)
PY
"$RESOURCE_DIR/runtime/bin/python3" -m pip install --disable-pip-version-check -r "$SOURCE_DIR/requirements.txt" -c "$SOURCE_DIR/requirements.lock"
"$RESOURCE_DIR/runtime/bin/python3" -m pip check
cp "$SOURCE_DIR/bridge.py" "$RESOURCE_DIR/bridge.py"
cp "$SOURCE_DIR/wireless.py" "$RESOURCE_DIR/wireless.py"
cp "$SOURCE_DIR/../LISEZ-MOI.md" "$RESOURCE_DIR/LISEZ-MOI.md"
cp "$SOURCE_DIR/LICENSE" "$RESOURCE_DIR/LICENSE"
cp "$SOURCE_DIR/Repere.icns" "$RESOURCE_DIR/Repere.icns"
mkdir -p "$RESOURCE_DIR/Map"
cp "$SOURCE_DIR/Map/"* "$RESOURCE_DIR/Map/"

xcrun swiftc -parse-as-library -swift-version 5 -O -target "$(uname -m)-apple-macosx14.0" \
  -module-cache-path "${REPERE_SWIFT_CACHE:-$BUILD_DIR/swift-cache}" \
  -framework SwiftUI -framework CoreLocation -framework AppKit -framework WebKit \
  "$SOURCE_DIR/Repere.swift" -o "$APP_DIR/Contents/MacOS/Repere"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>Repere</string>
  <key>CFBundleIdentifier</key><string>local.repere.location</string>
  <key>CFBundleName</key><string>Repère</string>
  <key>CFBundleDisplayName</key><string>Repère</string>
  <key>CFBundleIconFile</key><string>Repere</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.3</string>
  <key>CFBundleVersion</key><string>4</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict></plist>
PLIST

# Local ad-hoc signature; this is not an App Store/notarized distribution.
codesign --force --sign - "$APP_DIR"
codesign --verify --deep --strict "$APP_DIR"
echo "App construite : $APP_DIR"
