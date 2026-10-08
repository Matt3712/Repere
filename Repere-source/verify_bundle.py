#!/usr/bin/env python3
"""Audit a standalone Repère .app without connecting to an iPhone.

Usage: python3 verify_bundle.py /path/to/Repere.app
The default includes a temporary relocation and isolated Python smoke tests.
Exit 1 means the bundle is not ready for standalone distribution.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile


MACHO_MAGIC = {bytes.fromhex(value) for value in (
    "feedface", "cefaedfe", "feedfacf", "cffaedfe",
    "cafebabe", "bebafeca", "cafebabf", "bfbafeca")}
SYSTEM_ROOTS = ("/System/Library/", "/usr/lib/")
LICENSE_NAME = re.compile(r"^(licen[cs]e|copying)(?:[._-].*)?$", re.I)
MACOS_TARGET = (14, 0, 0)


def command(arguments, **kwargs):
    result = subprocess.run(arguments, text=True, capture_output=True, timeout=90, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name}: {(result.stderr or result.stdout).strip()[-1400:]}")
    return result.stdout


def inside(path, root):
    return path.resolve().is_relative_to(root.resolve())


def system_path(value):
    return any(value.startswith(root) for root in SYSTEM_ROOTS)


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def macos_version(value):
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", value):
        raise ValueError(f"Invalid macOS deployment version: {value}")
    components = tuple(map(int, value.split(".")))
    return components + (0,) * (3 - len(components))


def check_macos_target(load_commands, label, errors):
    versions = []
    for platform, version in re.findall(
            r"cmd LC_BUILD_VERSION\s+cmdsize \d+\s+platform (\S+)\s+minos ([\d.]+)", load_commands):
        if platform.upper() not in ("1", "MACOS"):
            errors.append(f"Non-macOS arm64 platform: {label} → {platform}")
        else:
            versions.append(version)
    versions.extend(re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)", load_commands))
    if not versions:
        errors.append(f"No macOS minimum-version metadata in arm64 image: {label}")
    for version in versions:
        if macos_version(version) > MACOS_TARGET:
            errors.append(f"Requires macOS {version}, above target 14.0: {label}")


def has_metadata_license(header, description):
    """Recognize complete notices in wheel metadata, not just SPDX labels."""
    text = header + "\n" + description
    upper = text.upper()
    if len(text) > 200 and "COPYRIGHT" in upper:
        if "Permission is hereby granted" in text and "THE SOFTWARE IS PROVIDED" in upper:
            return True
        if "Redistribution and use" in text and "THIS SOFTWARE IS PROVIDED" in upper:
            return True
        if "GNU GENERAL PUBLIC LICENSE" in upper and "TERMS AND CONDITIONS" in upper:
            return True
    # Some authors distribute an explicit public-domain declaration instead
    # of a longer licence. Require it in both the field and the description.
    return header.strip().casefold() == "public domain" and bool(re.search(
        r"(?im)^\s*(?:#+\s*)?licen[cs]e\s*\n(?:[-=]+\s*\n)?\s*Public Domain(?:\s|$)", description))


def inspect_macho(app, executable, python, errors):
    """Check all images, including extension modules not loaded by smoke tests."""
    images, architectures = {}, set()
    for directory, dirs, files in os.walk(app, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                try:
                    if path.readlink().is_absolute() or not inside(path, app) or not path.exists():
                        errors.append(f"Symlink not relocatable: {path.relative_to(app)} → {path.readlink()}")
                except (OSError, RuntimeError) as exc:
                    errors.append(f"Invalid symlink {path.relative_to(app)}: {exc}")
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            with path.open("rb") as stream:
                if stream.read(4) not in MACHO_MAGIC:
                    continue
            archs = set(command(["/usr/bin/lipo", "-archs", str(path)]).split())
            architectures.update(archs)
            if "arm64" not in archs:
                errors.append(f"No arm64 slice: {path.relative_to(app)} ({', '.join(sorted(archs))})")
            slice_args = ["-arch", "arm64"] if "arm64" in archs else []
            dependencies = re.findall(r"^\s+(.+?) \(compatibility version", command(["/usr/bin/otool", *slice_args, "-L", str(path)]), re.M)
            load_commands = command(["/usr/bin/otool", *slice_args, "-l", str(path)])
            if "arm64" in archs:
                check_macos_target(load_commands, str(path.relative_to(app)), errors)
            # otool -L also prints LC_ID_DYLIB, which names this image rather
            # than a dependency. Wheels often keep a build-time install ID.
            identifiers = re.findall(r"cmd LC_ID_DYLIB\s+cmdsize \d+\s+name (.+?) \(offset", load_commands)
            dependencies = [item for item in dependencies if item not in identifiers]
            rpaths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset", load_commands)
            images[path] = (dependencies, rpaths)

    def expand(value, image):
        context = python.parent if inside(image, app / "Contents/Resources/runtime") else executable.parent
        for prefix, base in (("@loader_path", image.parent), ("@executable_path", context)):
            if value == prefix or value.startswith(prefix + "/"):
                resolved = (base / value[len(prefix):].lstrip("/")).resolve()
                return str(resolved) if inside(resolved, app) else None
        return value if system_path(value) else None

    # Resolve declared runpaths in their own loader contexts. Dynamic imports
    # below additionally exercise the Python loader and its actual dependencies.
    all_rpaths = set()
    for path, (_, rpaths) in images.items():
        for rpath in rpaths:
            resolved = expand(rpath, path)
            if resolved is None:
                errors.append(f"External/unresolved rpath: {path.relative_to(app)} → {rpath}")
            else:
                all_rpaths.add(resolved)
    for path, (dependencies, _) in images.items():
        for dependency in dependencies:
            if system_path(dependency):
                continue
            if dependency.startswith("@rpath/"):
                candidates = [str(Path(base) / dependency[len("@rpath/"):]) for base in all_rpaths]
            else:
                resolved = expand(dependency, path)
                candidates = [resolved] if resolved else []
            if not any((system_path(value) and (Path(value).is_file() or value.startswith("/usr/lib/swift/libswift"))) or
                       (inside(Path(value), app) and Path(value).is_file()) for value in candidates):
                errors.append(f"External/unresolved dependency: {path.relative_to(app)} → {dependency}")
    if not images:
        errors.append("No Mach-O binaries found")
    return len(images), architectures


PROBE = r'''
import importlib, importlib.metadata, json, pathlib, runpy, site, sys
resources = pathlib.Path(sys.argv[1])
for module in (
    "_ssl", "_sqlite3", "_lzma", "_bz2", "_ctypes", "_cffi_backend",
    "cryptography.hazmat.bindings._rust", "qh3", "psutil", "pydantic_core", "PIL._imaging",
    "pymobiledevice3.remote.userspace_tunnel", "pymobiledevice3.remote.native_tunnel",
    "pymobiledevice3.services.dvt.instruments.location_simulation",
    "pymobiledevice3.services.mobile_image_mounter", "pymobiledevice3.services.amfi",
):
    importlib.import_module(module)
runpy.run_path(str(resources / "bridge.py"), run_name="repere_package_check")
wireless = importlib.import_module("wireless")
distributions = []
for dist in importlib.metadata.distributions():
    distributions.append({"name": dist.metadata["Name"], "version": dist.version,
                          "files": [str(dist.locate_file(item)) for item in dist.files or ()],
                          "license_text": dist.metadata.get("License", ""),
                          "license_description": str(dist.metadata.get_payload())})
print(json.dumps({"executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
                  "path": sys.path, "isolated": sys.flags.isolated,
                  "ignore_environment": sys.flags.ignore_environment,
                  "no_user_site": sys.flags.no_user_site, "enable_user_site": site.ENABLE_USER_SITE,
                  "wireless": wireless.__file__, "version": list(sys.version_info[:2]),
                  "distributions": distributions}))
'''


def inspect_runtime(app, work, errors):
    resources = app / "Contents/Resources"
    python = resources / "runtime/bin/python3"
    hostile = work / "hostile environnement"
    hostile.mkdir()
    (hostile / "sitecustomize.py").write_text("raise RuntimeError('HOSTILE SITE LOADED')\n")
    (hostile / "pymobiledevice3.py").write_text("raise RuntimeError('HOSTILE MODULE LOADED')\n")
    (hostile / "wireless.py").write_text("raise RuntimeError('HOSTILE MODULE LOADED')\n")
    environment = dict(os.environ, PYTHONHOME=str(hostile), PYTHONPATH=str(hostile),
                       PYTHONUSERBASE=str(hostile / "user-site"))
    output = command([str(python), "-I", "-B", "-c", PROBE, str(resources)], cwd=hostile, env=environment)
    report = json.loads(output)
    if report["version"] != [3, 14]:
        errors.append(f"Unexpected Python version: {report['version']}")
    for name in ("executable", "prefix", "base_prefix", "wireless"):
        if not inside(Path(report[name]), app):
            errors.append(f"Python {name} escapes bundle: {report[name]}")
    for entry in report["path"]:
        if not entry or not (inside(Path(entry), app) or system_path(entry)):
            errors.append(f"External Python sys.path: {entry!r}")
    if not all(report[name] for name in ("isolated", "ignore_environment", "no_user_site")) or report["enable_user_site"]:
        errors.append("Python isolation/user-site flags are incorrect")
    dependencies = {normalized(dist["name"]): dist["version"] for dist in report["distributions"]}
    if dependencies.get("pymobiledevice3") != "11.20.2":
        errors.append("pymobiledevice3 must be pinned to 11.20.2")
    help_text = command([str(python), "-I", "-B", "-u", str(resources / "bridge.py"), "--help"],
                        cwd=hostile, env=environment)
    if "random" not in help_text or "route" not in help_text:
        errors.append("Bridge --help did not expose the expected commands")

    centralized = resources / "Licenses"
    for dist in report["distributions"]:
        files = [Path(item) for item in dist["files"]]
        collected = centralized / normalized(dist["name"])
        if collected.is_dir():
            files.extend(path for path in collected.rglob("*") if path.is_file())
        texts = [path for path in files if inside(path, app) and LICENSE_NAME.match(path.name)
                 and path.is_file() and path.stat().st_size > 40]
        has_metadata_text = has_metadata_license(dist["license_text"], dist["license_description"])
        if not texts and not has_metadata_text:
            errors.append(f"No bundled license text identified: {dist['name']} {dist['version']}")
    required = [resources / "LICENSE", resources / "Map/LICENSE-Leaflet"]
    for license_file in required:
        if not license_file.is_file():
            errors.append(f"Missing license: {license_file.relative_to(app)}")
    python_licenses = list((resources / "runtime").glob("LICENSE*"))
    python_license_roots = [resources / "runtime/licenses"]
    if centralized.is_dir():
        python_license_roots.extend(path for path in centralized.iterdir()
                                    if path.is_dir() and path.name.casefold() in ("python", "cpython"))
    for root in python_license_roots:
        if root.exists():
            python_licenses.extend(path for path in root.rglob("*") if path.is_file())
    if not any("PYTHON" in path.read_text(errors="replace").upper() and
               "SOFTWARE FOUNDATION" in path.read_text(errors="replace").upper() for path in python_licenses):
        errors.append("No bundled Python Software Foundation license text identified")
    return len(dependencies)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--no-relocate", action="store_true", help="Audit the original path only; skip the relocation copy")
    args = parser.parse_args()
    app = args.app.resolve()
    errors = []
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        if macos_version(info.get("LSMinimumSystemVersion", "0")) > MACOS_TARGET:
            errors.append("Info.plist requires macOS newer than target 14.0")
        executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
        python = app / "Contents/Resources/runtime/bin/python3"
        for required in (executable, python, app / "Contents/Resources/bridge.py", app / "Contents/Resources/wireless.py"):
            if not required.is_file():
                errors.append(f"Missing file: {required.relative_to(app)}")
        images, architectures = inspect_macho(app, executable, python, errors)
        command(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
        with tempfile.TemporaryDirectory(prefix="repere-distribution-") as temporary:
            work = Path(temporary)
            relocated = app
            if not args.no_relocate:
                relocated = work / "Déplacement indépendant avec espaces" / "Repère vérifiée.app"
                shutil.copytree(app, relocated, symlinks=True)
            distributions = inspect_runtime(relocated, work, errors)
        print(f"Audited {images} Mach-O images ({', '.join(sorted(architectures))}); {distributions} Python distributions.")
        print("Relocation skipped." if args.no_relocate else "Relocated Python, imports, and bridge --help tested under hostile Python environment.")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        errors.append(str(exc))
    for error in dict.fromkeys(errors):
        print(f"FAIL: {error}", file=sys.stderr)
    if errors:
        return 1
    label = "arm64 bundle audit (relocation skipped)" if args.no_relocate else "standalone arm64 bundle audit"
    print(f"PASS: {label}. No iPhone access performed; Intel execution is not validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
