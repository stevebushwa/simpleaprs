# PyInstaller spec for the Windows and macOS builds of the desktop app (the Linux AppImage is built by
# scripts/build-appimage.sh). Run from the repository root with simpleaprs[gui] installed:
#
#   python packaging/make_icons.py build/icons
#   SIMPLEAPRS_ICONS=build/icons pyinstaller --noconfirm packaging/simpleaprs.spec
#
# -> dist/simpleaprs/ (Windows) or dist/simpleaprs.app (macOS)
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

mac = sys.platform == "darwin"
icons = os.environ.get("SIMPLEAPRS_ICONS", "")
icon = os.path.join(icons, "simpleaprs.icns" if mac else "simpleaprs.ico") if icons else None
version = os.environ.get("SIMPLEAPRS_VERSION", "0.1.0")

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    datas=collect_data_files("simpleaprs"),  # the web page, map files and alert sounds
    excludes=["sounddevice"],                # the terminal server is Linux only
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="simpleaprs", console=False, icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, name="simpleaprs")
if mac:
    app = BUNDLE(
        coll, name="simpleaprs.app", icon=icon, bundle_identifier="io.github.stevebushwa.simpleaprs", version=version,
        info_plist={
            "CFBundleDisplayName": "simpleaprs",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "simpleaprs listens to your radio's audio to decode APRS packets.",
        },
    )
