#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
OUTPUT_DIR="${PWD:h}"
APP_DIR="$OUTPUT_DIR/Repere.app"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP_DIR/Contents/Info.plist")"
DMG_PATH="$OUTPUT_DIR/Repere-$VERSION-Mac.dmg"
PACKAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp/}repere-package.XXXXXX")"
trap 'rm -rf "$PACKAGE_DIR"' EXIT

codesign --verify --deep --strict "$APP_DIR"
mkdir -p "$PACKAGE_DIR/volume"
ditto "$APP_DIR" "$PACKAGE_DIR/volume/Repere.app"
ln -s /Applications "$PACKAGE_DIR/volume/Applications"
cp "$OUTPUT_DIR/INSTALLATION.txt" "$PACKAGE_DIR/volume/INSTALLATION.txt"
hdiutil create -volname "Repère" -srcfolder "$PACKAGE_DIR/volume" \
  -format UDZO -ov "$DMG_PATH"
hdiutil verify "$DMG_PATH"
echo "Installation créée : $DMG_PATH"
