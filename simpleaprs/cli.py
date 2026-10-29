"""Command line entry point: the desktop app by default, a terminal network server with --headless."""
from __future__ import annotations

import argparse
import sys

EPILOG = """\
Headless mode shares the radio over https: open the web page it prints in any browser on the network (the browser
warns about the certificate, which the server makes for itself: continue past it). The first visit creates your
login, and the page's Settings has everything else. Desktop apps follow it with the access token shown under
Settings > Access (Settings > "Connect to another simpleaprs"). Keep it on a LAN or VPN: the certificate hides
the traffic but proves nothing about who is at the other end. --http serves plain http instead (browsers then
won't allow notifications). --setup asks the setup questions in the terminal instead. Other options apply to this run only,
unless --save is given.
"""


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="simpleaprs", description="APRS with a built-in sound-card modem.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)  # for the build: can this install start at all?
    ap.add_argument("--headless", "--server", action="store_true", help="run in the terminal as a network server, no window")
    ap.add_argument("--setup", action="store_true", help="(headless) answer the setup questions in the terminal (the web page's Settings does the same)")
    ap.add_argument("--reset-login", action="store_true", help="(headless) forget the web page's login: the next visitor creates a new one")
    ap.add_argument("--list-devices", action="store_true", help="(headless) print the audio devices and exit")
    ap.add_argument("--http", action="store_true", help="(headless) serve plain http, not https (browsers only allow notifications on https)")
    ap.add_argument("-q", "--quiet", action="store_true", help="(headless) don't print every packet heard")
    ap.add_argument("--save", action="store_true", help="(headless) keep the options below as the saved settings")
    g = ap.add_argument_group("settings (headless)")
    g.add_argument("--callsign", help="your callsign, e.g. N0CALL-7")
    g.add_argument("--lat", type=float, help="latitude, decimal degrees")
    g.add_argument("--lon", type=float, help="longitude, decimal degrees")
    g.add_argument("--symbol", help="two-character APRS symbol, e.g. /-")
    g.add_argument("--comment", help="beacon comment")
    g.add_argument("--path", help="digipeater path, e.g. WIDE1-1,WIDE2-1")
    g.add_argument("--input-device", metavar="NAME", help="audio input, as --list-devices shows it")
    g.add_argument("--output-device", metavar="NAME", help="audio output, as --list-devices shows it")
    g.add_argument("--lead-ms", type=int, help="VOX lead-in in milliseconds")
    g.add_argument("--beacon-minutes", type=int, help="auto beacon interval, 0 = only when a client asks")
    g.add_argument("--port", type=int, help="network port to serve on")
    g.add_argument("--nomad-url", metavar="URL", help="Project N.O.M.A.D. map server, e.g. http://nomad.local:8080")
    g.add_argument("--igate", action=argparse.BooleanOptionalAction, default=None,
                   help="pass what the radio hears to the internet (APRS-IS); needs --igate-passcode")
    g.add_argument("--igate-server", metavar="HOST:PORT", help="APRS-IS server, default rotate.aprs2.net:14580")
    g.add_argument("--igate-passcode", metavar="CODE", help="APRS-IS passcode for your callsign")
    g.add_argument("--igate-to-rf", action=argparse.BooleanOptionalAction, default=None,
                   help="also transmit internet messages for stations heard directly here (the radio transmits on its own)")
    g.add_argument("--igate-radius", type=int, metavar="KM", help="how far from you to look for those messages, default 50")
    g.add_argument("--digipeater", choices=("off", "fill", "wide"),
                   help="repeat packets that ask for it: fill = WIDE1-1 only, wide = also WIDE2-1/WIDE2-2 (the radio transmits on its own)")
    g.add_argument("--no-tx", action="store_true", help="don't let the web page or clients transmit through this radio")
    g.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="also accept requests addressed to this host name (the server refuses names that don't look like "
                        "a LAN machine); can be repeated")
    return ap.parse_args(argv)


# command line option -> Config field
OVERRIDES = {"callsign": "callsign", "lat": "lat", "lon": "lon", "symbol": "symbol", "comment": "comment",
             "path": "path", "input_device": "input_device", "output_device": "output_device",
             "lead_ms": "tx_lead_ms", "beacon_minutes": "beacon_minutes", "port": "share_port", "nomad_url": "nomad_url",
             "igate": "igate_enabled", "igate_server": "igate_server", "igate_passcode": "igate_passcode",
             "igate_to_rf": "igate_to_rf", "igate_radius": "igate_radius_km"}


def apply_overrides(cfg, args: argparse.Namespace) -> None:
    """Put the command line options on cfg."""
    for opt, field in OVERRIDES.items():
        v = getattr(args, opt)
        if v is not None:
            setattr(cfg, field, v.upper() if opt == "callsign" else v)
    if args.digipeater:
        cfg.digi_enabled, cfg.digi_wide = args.digipeater != "off", args.digipeater == "wide"
    if args.no_tx:
        cfg.share_allow_tx = False


def headless(args: argparse.Namespace) -> int:
    from . import headless as hl
    from .config import PATH, Config

    cfg = Config.load()
    apply_overrides(cfg, args)
    if args.symbol is not None and len(cfg.symbol) != 2:
        sys.exit("--symbol is two characters: the table (/ or \\) then the code, like /-.")
    if args.reset_login:
        from piwebkit.auth import Auth

        from .config import AUTH_PATH
        Auth(AUTH_PATH).reset()
        print("The web page's login has been forgotten. Open the page to create a new one.")
    if args.setup:
        if not sys.stdin.isatty():
            sys.exit("--setup asks questions, so it needs a terminal. Everything it asks is also in the web page's Settings.")
        try:
            cfg = hl.setup(cfg)
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nSetup cancelled, nothing was saved.")
        args.save = False  # already saved
    if args.save:
        cfg.save()
        print(f"Saved to {PATH}")
    return hl.run(cfg, args.quiet, tuple(args.allow_host), tls=not args.http)


SERVER_ONLY_LINUX = ("The terminal server (--headless, --list-devices) only runs on Linux, which is where a Raspberry Pi or other\n"
                     "single-board computer lives. On this system use the desktop app, and point it at the server\n"
                     "(Settings > Network > Connect to another simpleaprs).")


def selftest() -> int:
    """Used by the build to check a packaged app: the Qt parts and the web files it needs are all in there."""
    from pathlib import Path

    from piwebkit.maps import WEB as SHARED_WEB

    from . import app  # noqa: F401  (imports Qt, Qt WebEngine and Qt Multimedia)
    from .mapserver import WEB
    for root, names in ((WEB, ("app.html", "map.js", "frequencies.js", "icon.svg", "icon-512.png")), (SHARED_WEB, ("login.html", "maplibre-gl.js", "pmtiles.js"))):
        for name in names:
            if not (root / name).is_file():
                print(f"missing {Path(root / name)}", file=sys.stderr)
                return 1
    print("simpleaprs selftest ok")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
    if args.selftest:
        sys.exit(selftest())
    if (args.headless or args.list_devices) and not sys.platform.startswith("linux"):
        sys.exit(SERVER_ONLY_LINUX)
    if args.list_devices:
        from .soundio import device_names
        print("Inputs:\n  " + "\n  ".join(device_names(True) or ["(none)"]))
        print("Outputs:\n  " + "\n  ".join(device_names(False) or ["(none)"]))
        return
    if args.headless:
        sys.exit(headless(args))
    try:
        from .app import main as gui
    except ImportError as e:
        sys.exit(f"The desktop app needs PySide6 ({e}).\nInstall it with:  pip install 'simpleaprs[gui]'\n"
                 "or run the terminal server instead:  simpleaprs --headless")
    gui()


if __name__ == "__main__":
    main()
