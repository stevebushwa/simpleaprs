#!/bin/bash
# Bundle simpleaprs with PyInstaller and package it as an AppImage.
#
#   ./scripts/build-appimage.sh            # -> dist/simpleaprs-<arch>.AppImage
#
# Env overrides:
#   APPIMAGETOOL    appimagetool binary (default: the one on PATH)
#   VENV            build venv (default: build/venv)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m)"
BUILD="$ROOT/build"
VENV="${VENV:-$BUILD/venv}"
APPDIR="$BUILD/AppDir"
DIST="$ROOT/dist"
APPIMAGETOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$APPIMAGETOOL" ] || [ ! -x "$APPIMAGETOOL" ]; then
    echo "appimagetool not found: put it on PATH or set APPIMAGETOOL=/path/to/appimagetool" >&2
    exit 1
fi

echo "=== venv"
if [ ! -x "$VENV/bin/python" ]; then
    # Some hosts ship python3 without ensurepip (no python3-venv): bootstrap pip by hand.
    if python3 -m venv "$VENV" 2>/dev/null; then :; else
        rm -rf "$VENV"
        python3 -m venv --without-pip "$VENV"
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$BUILD/get-pip.py"
        "$VENV/bin/python" "$BUILD/get-pip.py" -q
    fi
fi
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q "$ROOT[all]" pyinstaller

echo "=== pyinstaller"
"$VENV/bin/pyinstaller" --noconfirm --clean --name simpleaprs \
    --distpath "$BUILD/pyi" --workpath "$BUILD/pyi-work" --specpath "$BUILD" \
    --collect-data simpleaprs \
    "$ROOT/packaging/launcher.py"

echo "=== AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a "$BUILD/pyi/simpleaprs" "$APPDIR/usr/lib/simpleaprs"
# libgbm must match the Mesa drivers of the machine it runs on. The copy PyInstaller picked up from the build
# host is too old for a newer Mesa ("did not find extension DRI_Mesa", "Failed to initialize GBM device",
# and the map stays blank), so use the one the system has.
find "$APPDIR/usr/lib/simpleaprs" -name 'libgbm.so*' -delete
cp "$ROOT/packaging/simpleaprs.desktop" "$APPDIR/simpleaprs.desktop"
cp "$ROOT/simpleaprs/web/icon.svg" "$APPDIR/simpleaprs.svg"
ln -sf simpleaprs.svg "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<'EOS'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/simpleaprs/simpleaprs" "$@"
EOS
chmod +x "$APPDIR/AppRun"

echo "=== appimagetool"
mkdir -p "$DIST"
OUT="$DIST/simpleaprs-$ARCH.AppImage"
ARCH="$ARCH" "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$OUT"
ls -lh "$OUT"
