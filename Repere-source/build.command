#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
SOURCE_DIR="$PWD"
OUTPUT_DIR="${SOURCE_DIR:h}"
APP_DIR="$OUTPUT_DIR/Repere.app"
BUILD_DIR="$(mktemp -d "$OUTPUT_DIR/.repere-build.XXXXXX")"
STAGED_APP="$BUILD_DIR/Repere.app"
RESOURCE_DIR="$STAGED_APP/Contents/Resources"
BACKUP_APP=""
cleanup() {
  if [[ -n "$BACKUP_APP" && ! -e "$APP_DIR" && -d "$BACKUP_APP" ]]; then
    mv "$BACKUP_APP" "$APP_DIR"
  fi
  rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

[[ "$(uname -m)" == arm64 ]] || { echo "Construction Apple Silicon uniquement." >&2; exit 1; }
mkdir -p "$BUILD_DIR/swift-cache" "$STAGED_APP/Contents/MacOS" "$RESOURCE_DIR"
zsh "$SOURCE_DIR/bootstrap_runtime.command" "$RESOURCE_DIR"
PYTHON_BIN="$RESOURCE_DIR/runtime/bin/python3"
cp "$SOURCE_DIR/bridge.py" "$RESOURCE_DIR/bridge.py"
cp "$SOURCE_DIR/wireless.py" "$RESOURCE_DIR/wireless.py"
cp "$SOURCE_DIR/../LISEZ-MOI.md" "$RESOURCE_DIR/LISEZ-MOI.md"
cp "$SOURCE_DIR/LICENSE" "$RESOURCE_DIR/LICENSE"
cp "$SOURCE_DIR/Repere.icns" "$RESOURCE_DIR/Repere.icns"
mkdir -p "$RESOURCE_DIR/Map"
cp "$SOURCE_DIR/Map/"* "$RESOURCE_DIR/Map/"

xcrun swiftc -parse-as-library -swift-version 5 -O -target arm64-apple-macosx14.0 \
  -module-cache-path "${REPERE_SWIFT_CACHE:-$BUILD_DIR/swift-cache}" \
  -framework SwiftUI -framework CoreLocation -framework AppKit -framework WebKit \
  "$SOURCE_DIR/Repere.swift" -o "$STAGED_APP/Contents/MacOS/Repere"

cat > "$STAGED_APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>Repere</string>
  <key>CFBundleIdentifier</key><string>local.repere.location</string>
  <key>CFBundleName</key><string>Repère</string>
  <key>CFBundleDisplayName</key><string>Repère</string>
  <key>CFBundleIconFile</key><string>Repere</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.4</string>
  <key>CFBundleVersion</key><string>5</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict></plist>
PLIST

# Sign actual Mach-O leaves before the outer bundle, never using --deep to sign.
# This ad-hoc signature verifies integrity; it is not Developer ID notarization.
"$PYTHON_BIN" -I -B "$SOURCE_DIR/sign_bundle.py" "$STAGED_APP"
codesign --verify --deep --strict "$STAGED_APP"
"$PYTHON_BIN" -I -B "$SOURCE_DIR/verify_bundle.py" "$STAGED_APP"

# All verification runs before replacement. Preserve the prior app for rollback.
if [[ -e "$APP_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR/.repere-backups"
  BACKUP_APP="$OUTPUT_DIR/.repere-backups/Repere-$(date -u +%Y%m%dT%H%M%SZ)-$$.app"
  mv "$APP_DIR" "$BACKUP_APP"
fi
mv "$STAGED_APP" "$APP_DIR"
echo "App autonome construite : $APP_DIR"
[[ -z "$BACKUP_APP" ]] || echo "Version précédente préservée : $BACKUP_APP"
