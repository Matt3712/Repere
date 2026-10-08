#!/usr/bin/env python3
"""Ad-hoc sign nested Mach-O files first, then the enclosing macOS app."""
from pathlib import Path
import subprocess
import sys


app = Path(sys.argv[1]).resolve()
magic = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
leaves = []
for item in app.rglob("*"):
    if item.is_symlink() or not item.is_file():
        continue
    with item.open("rb") as stream:
        if stream.read(4) in magic:
            leaves.append(item)
for item in sorted(leaves, key=lambda path: len(path.parts), reverse=True):
    result = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(item)],
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"Signature impossible : {item}\n{result.stderr}")
subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(app)], check=True)
print(f"Signatures internes appliquées : {len(leaves)} binaires Mach-O, puis application.")
