"""Command line entry point: the MeshCore web page for a node on a serial port."""
from __future__ import annotations

import argparse
import signal
import sys
import tempfile
import threading
from pathlib import Path

EPILOG = """\
Serves a web page for a MeshCore companion node plugged into this machine over USB serial: nodes on a map (from
Project N.O.M.A.D., so it works offline), channels, direct messages, the radio's packet log, and the node's
radio, channel and firmware settings, plus logging in to repeaters and room servers. The first visit creates your login. Everything else is on the page's Settings tab.
It is served over https with a certificate the server makes for itself (install it on a device to stop the browser warning
and to allow notifications); --http serves plain HTTP instead. Keep it on a LAN or VPN.
Options apply to this run only, unless --save is given.
"""


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="simplemesh", description="A web page for a MeshCore node.", epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial-port", metavar="DEV", help="the node's serial device, e.g. /dev/ttyACM0 (default: find it)")
    ap.add_argument("--baud", type=int, help="serial speed, default 115200")
    ap.add_argument("--port", type=int, help="web port to serve on, default 8074")
    ap.add_argument("--nomad-url", metavar="URL", help="Project N.O.M.A.D. map server, default http://localhost:8080; blank for no map")
    ap.add_argument("--home", metavar="LAT,LON", help="where the map starts when nothing on it has a position")
    ap.add_argument("--save", action="store_true", help="keep the options above as the saved settings")
    ap.add_argument("--reset-login", action="store_true", help="forget the web page's login: the next visitor creates a new one")
    ap.add_argument("--list-ports", action="store_true", help="print the serial devices that could be the node, and exit")
    ap.add_argument("--demo", action="store_true", help="no radio: a made-up mesh, to look at the page (nothing is saved)")
    ap.add_argument("--demo-at", metavar="LAT,LON", help="(with --demo) where the made-up mesh is (default: --home, else the middle of the US)")
    ap.add_argument("--http", action="store_true", help="serve plain http, not https (browsers only allow notifications and installing the page as an app on https)")
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="also accept requests addressed to this host name (the server refuses names that don't look like "
                         "a LAN machine); can be repeated")
    return ap.parse_args(argv)


def run(cfg, demo: bool, allowed: tuple[str, ...], demo_at: tuple[float, float] | None = None, tls: bool = True) -> int:
    from piwebkit.auth import Auth
    from piwebkit.hub import Hub
    from piwebkit import tls as piwebkit_tls
    from piwebkit.maps import WEB as SHARED_WEB
    from piwebkit.maps import MapProxy
    from piwebkit.server import WebServer, lan_ip
    from piwebkit.tilecache import TileCache

    from . import config
    from .node import MeshNode, candidate_ports
    from .store import Store
    from .webui import WEB, MeshWeb

    if demo:
        scratch = Path(tempfile.mkdtemp(prefix="simplemesh-demo-"))
        store = Store(scratch / "nodes.json", scratch / "messages.json")
        auth = Auth(scratch / "auth.json")
    else:
        store = Store(config.NODES_PATH, config.MESSAGES_PATH)
        auth = Auth(config.AUTH_PATH)
    hub = Hub()
    connector, fetch, flasher = None, None, None
    if demo:
        from .demo import DemoMesh, demo_fetch, demo_flasher
        lat, lon = demo_at or cfg.home or (39.50, -98.35)
        fetch, flasher = demo_fetch, demo_flasher

        async def connector(port, baud):
            return DemoMesh(lat, lon)
    node = MeshNode(cfg, hub, store, connector=connector, ports=(lambda: ["demo"]) if demo else candidate_ports,
                    fetch=fetch, flasher=flasher)
    cache = TileCache(config.TILES_DIR, max_bytes=cfg.map_cache_mb * 1024 * 1024) if cfg.map_cache else None
    proxy = MapProxy(cfg.nomad_url, cache, web=(WEB, SHARED_WEB))
    web = MeshWeb(hub, node, proxy, auth, cfg)
    tls_dir = scratch / "tls" if demo else config.TLS_DIR
    context = piwebkit_tls.context(tls_dir, "simplemesh") if tls else None
    if tls and not context:
        print("Could not make a certificate (is the openssl command installed?): serving plain http instead.", file=sys.stderr)
    web.cert_file = tls_dir / "cert.pem" if context else None  # offered for download (Settings > Connection)
    try:
        server = WebServer(cfg.web_port, web, allowed_hosts=allowed, banner="simplemesh\n", tls=context)
    except OSError as e:
        print(f"Can't serve on port {cfg.web_port}: {e}", file=sys.stderr)
        return 1
    node.start()
    print(f"simplemesh{' (demo)' if demo else ''}: http{'s' if context else ''}://{lan_ip()}:{server.port}/", flush=True)
    if context:
        print("The browser will warn about the certificate (this server made it for itself): continue past it, or install it from "
              "Settings > Connection.", flush=True)
    print("Map server: " + (proxy.nomad or "none set (nodes are listed but there is no map)"), flush=True)
    if not auth.configured:
        print("No login yet: the first person to open the page creates it.", flush=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.wait(1):
        pass
    server.stop()
    node.stop()
    return 0


def latlon(text: str, flag: str) -> tuple[float, float]:
    try:
        lat, lon = (float(v) for v in text.split(","))
        assert -90 <= lat <= 90 and -180 <= lon <= 180
    except (ValueError, AssertionError):
        sys.exit(f"{flag} takes a latitude and longitude, like 29.2,-98.7")
    return lat, lon


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
    if not sys.platform.startswith("linux"):
        sys.exit("simplemesh serves a node on a serial port of a Linux machine, such as a Raspberry Pi.")
    try:
        import meshcore  # noqa: F401
    except ImportError:
        sys.exit("simplemesh needs the meshcore library:  pip install 'simpleaprs[mesh]'  (the mesh extra)")
    from .config import AUTH_PATH, PATH, Config
    from .node import all_ports, candidate_ports

    if args.list_ports:
        found = candidate_ports()
        print("\n".join(found) if found else "No USB serial devices found.")
        extra = [p for p in all_ports() if p not in found]
        if extra:
            print("Also present (the Pi's own UART; choose one in Settings or with --serial-port):\n  " + "\n  ".join(extra))
        return
    cfg = Config.load()
    if args.home:
        cfg.home_lat, cfg.home_lon = latlon(args.home, "--home")
    for opt, field in (("serial_port", "serial_port"), ("baud", "baud"), ("port", "web_port"), ("nomad_url", "nomad_url")):
        if getattr(args, opt) is not None:
            setattr(cfg, field, getattr(args, opt))
    if args.reset_login:
        from piwebkit.auth import Auth
        Auth(AUTH_PATH).reset()
        print("The web page's login has been forgotten. Open the page to create a new one.")
    if args.save:
        cfg.save()
        print(f"Saved to {PATH}")
    demo_at = latlon(args.demo_at, "--demo-at") if args.demo_at else None
    sys.exit(run(cfg, args.demo, tuple(args.allow_host), demo_at, tls=not args.http))


if __name__ == "__main__":
    main()
