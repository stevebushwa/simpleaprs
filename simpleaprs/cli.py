"""Command line entry point: the desktop app by default, a terminal network server with --headless."""
from __future__ import annotations

import argparse
import sys

EPILOG = """\
Headless mode shares the radio over HTTP: open the web page it prints in any browser on the network, or
point the desktop app at it (Settings > "Connect to another simpleaprs"). There is no token, so keep it on
a LAN or VPN. The first headless run asks a few questions and saves them; --setup asks them again. Other
options apply to this run only, unless --save is given.
"""


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="simpleaprs", description="APRS with a built-in sound-card modem.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headless", "--server", action="store_true", help="run in the terminal as a network server, no window")
    ap.add_argument("--setup", action="store_true", help="(headless) go through the setup questions again")
    ap.add_argument("--list-devices", action="store_true", help="(headless) print the audio devices and exit")
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
    g.add_argument("--no-tx", action="store_true", help="don't let the web page or clients transmit through this radio")
    g.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="also accept requests addressed to this host name (the server refuses names that don't look like "
                        "a LAN machine); can be repeated")
    return ap.parse_args(argv)


# command line option -> Config field
OVERRIDES = {"callsign": "callsign", "lat": "lat", "lon": "lon", "symbol": "symbol", "comment": "comment",
             "path": "path", "input_device": "input_device", "output_device": "output_device",
             "lead_ms": "tx_lead_ms", "beacon_minutes": "beacon_minutes", "port": "share_port", "nomad_url": "nomad_url"}


def apply_overrides(cfg, args: argparse.Namespace) -> None:
    """Put the command line options on cfg."""
    for opt, field in OVERRIDES.items():
        v = getattr(args, opt)
        if v is not None:
            setattr(cfg, field, v.upper() if opt == "callsign" else v)
    if args.no_tx:
        cfg.share_allow_tx = False


def headless(args: argparse.Namespace) -> int:
    from . import headless as hl
    from .config import PATH, Config

    cfg = Config.load()
    apply_overrides(cfg, args)
    if args.symbol is not None and len(cfg.symbol) != 2:
        sys.exit("--symbol is two characters: the table (/ or \\) then the code, like /-.")
    if args.setup or not cfg.configured:
        if not sys.stdin.isatty():
            sys.exit("simpleaprs is not set up yet. Run it once from a terminal to answer the setup questions,\n"
                     "or give --callsign (and --lat/--lon) on the command line.")
        try:
            cfg = hl.setup(cfg)
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nSetup cancelled, nothing was saved.")
        args.save = False  # already saved
    if args.save:
        cfg.save()
        print(f"Saved to {PATH}")
    return hl.run(cfg, args.quiet, tuple(args.allow_host))


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
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
