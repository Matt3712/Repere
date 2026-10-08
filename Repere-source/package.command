#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
SOURCE_DIR="$PWD"
OUTPUT_DIR="${SOURCE_DIR:h}"
APP_DIR="$OUTPUT_DIR/Repere.app"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP_DIR/Contents/Info.plist")"
DMG_PATH="$OUTPUT_DIR/Repere-$VERSION-AppleSilicon.dmg"
PACKAGE_DIR="$(mktemp -d "$OUTPUT_DIR/.repere-package.XXXXXX")"
trap 'rm -rf "$PACKAGE_DIR"' EXIT

codesign --verify --deep --strict "$APP_DIR"
"$APP_DIR/Contents/Resources/runtime/bin/python3" -I -B "$SOURCE_DIR/verify_bundle.py" "$APP_DIR" --no-relocate
mkdir -p "$PACKAGE_DIR/volume"
ditto "$APP_DIR" "$PACKAGE_DIR/volume/Repere.app"
ln -s /Applications "$PACKAGE_DIR/volume/Applications"
cp "$OUTPUT_DIR/INSTALLATION.txt" "$PACKAGE_DIR/volume/INSTALLATION.txt"
hdiutil create -volname "Repère $VERSION Apple Silicon" -srcfolder "$PACKAGE_DIR/volume" \
  -format UDZO -ov "$PACKAGE_DIR/Repere.dmg"
hdiutil verify "$PACKAGE_DIR/Repere.dmg"
mv -f "$PACKAGE_DIR/Repere.dmg" "$DMG_PATH"
echo "Installation créée : $DMG_PATH"
