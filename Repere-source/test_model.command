#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp/}repere-model.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT
python3 - "$TEST_DIR/ModelChecks.swift" <<'PY'
from pathlib import Path
import sys
model = Path('Repere.swift').read_text().split('struct LocationMap: NSViewRepresentable {', 1)[0]
Path(sys.argv[1]).write_text(model + '\n' + Path('test_model.swift').read_text())
PY
xcrun swiftc -parse-as-library -swift-version 5 \
  -module-cache-path "${REPERE_SWIFT_CACHE:-$TEST_DIR/swift-cache}" \
  -framework SwiftUI -framework CoreLocation -framework AppKit -framework WebKit \
  "$TEST_DIR/ModelChecks.swift" -o "$TEST_DIR/check-model"
"$TEST_DIR/check-model"
