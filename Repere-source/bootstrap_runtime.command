#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
SOURCE_DIR="$PWD"
RESOURCE_DIR="${1:?Usage: bootstrap_runtime.command APP_RESOURCES_DIR}"
MANIFEST="$SOURCE_DIR/runtime-manifest.json"
CACHE_DIR="${REPERE_BUILD_CACHE:-${TMPDIR:-/tmp/}repere-runtime-cache}"
mkdir -p "$CACHE_DIR" "$RESOURCE_DIR/runtime" "$RESOURCE_DIR/Licenses/Python"

if [[ "$(uname -m)" != arm64 ]]; then
  echo "Cette construction nécessite un Mac Apple Silicon (arm64)." >&2
  exit 1
fi
if [[ -e "$RESOURCE_DIR/runtime/bin/python3" ]]; then
  echo "Le runtime de destination existe déjà ; utilisez un dossier de construction neuf." >&2
  exit 1
fi

fetch_archive() {
  local section="$1"
  local filename="$(/usr/bin/plutil -extract "$section.filename" raw -o - "$MANIFEST")"
  local url="$(/usr/bin/plutil -extract "$section.url" raw -o - "$MANIFEST")"
  local checksum="$(/usr/bin/plutil -extract "$section.sha256" raw -o - "$MANIFEST")"
  local archive="$CACHE_DIR/$filename"
  if [[ -f "$archive" ]] && [[ "$(/usr/bin/shasum -a 256 "$archive" | /usr/bin/cut -d ' ' -f 1)" != "$checksum" ]]; then
    echo "Archive en cache invalide : $filename" >&2
    exit 1
  fi
  if [[ ! -f "$archive" ]]; then
    /usr/bin/curl --proto '=https' --tlsv1.2 --fail --location --retry 3 \
      --connect-timeout 20 --max-time 600 --output "$archive.part" "$url"
    [[ "$(/usr/bin/shasum -a 256 "$archive.part" | /usr/bin/cut -d ' ' -f 1)" == "$checksum" ]] || {
      echo "Échec de vérification SHA-256 : $filename" >&2; return 1
    }
    mv "$archive.part" "$archive"
  fi
  print -r -- "$archive"
}

RUNTIME_ARCHIVE="$(fetch_archive runtime)"
LICENSE_ARCHIVE="$(fetch_archive licenses)"
/usr/bin/tar -xzf "$RUNTIME_ARCHIVE" -C "$RESOURCE_DIR/runtime" --strip-components=1
# The install-only archive omits upstream native-library license texts. Extract
# those texts and the build metadata from the matching verified full archive.
/usr/bin/tar -xf "$LICENSE_ARCHIVE" -C "$RESOURCE_DIR/Licenses/Python" --strip-components=2 \
  --include='python/licenses/*'
/usr/bin/tar -xOf "$LICENSE_ARCHIVE" python/PYTHON.json > "$RESOURCE_DIR/Licenses/Python/PYTHON-build.json"
cp "$MANIFEST" "$RESOURCE_DIR/runtime-manifest.json"

PYTHON_BIN="$RESOURCE_DIR/runtime/bin/python3"
"$PYTHON_BIN" -I -B -c 'import platform, sys; assert sys.version_info[:3] == (3,14,8); assert platform.machine() == "arm64"; print("CPython autonome :", sys.version.split()[0])'
"$PYTHON_BIN" -I -B -m pip --isolated install --disable-pip-version-check --no-compile \
  --cache-dir "$CACHE_DIR/pip" -r "$SOURCE_DIR/requirements.lock"
"$PYTHON_BIN" -I -B -m pip --isolated check
"$PYTHON_BIN" -I -B "$SOURCE_DIR/prepare_runtime.py" "$RESOURCE_DIR" "$SOURCE_DIR/requirements.lock"
