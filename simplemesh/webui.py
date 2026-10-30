"""The page, as piwebkit's server serves it (its `web` argument): the login, map proxy and event stream come from
piwebkit, with MeshCore's own requests on top. Replies are JSON; a request the node can't do answers 409 with the
reason, which the page shows.
"""
from __future__ import annotations

import re
from pathlib import Path

from piwebkit.hub import Hub
from piwebkit.maps import MapProxy
from piwebkit.webbase import BaseWebUI

from .config import Config
from .firmware import FirmwareError
from .node import BANDWIDTHS, PRESETS, MeshError, MeshNode, all_ports, candidate_ports

WEB = Path(__file__).parent / "web"
KEY = re.compile(r"^[0-9a-f]{64}$")
PREFIX = re.compile(r"^[0-9a-f]{12}$")
BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
UPLOAD_JSON_BYTES = 6 * 1024 * 1024  # a firmware file of up to 4 MiB, as base64 in JSON


class MeshWeb(BaseWebUI):
    cookie_name = "simplemesh_session"
    app_name = "simplemesh"

    def __init__(self, hub: Hub, node: MeshNode, proxy: MapProxy, auth, cfg: Config) -> None:
        super().__init__(hub, proxy, auth)
        self.node, self.cfg = node, cfg
        self.cert_file: Path | None = None  # the certificate the page is served with over https, for installing on a device
        routes = {"/api/send": self._send, "/api/advert": self._advert, "/api/profile": self._profile,
                  "/api/node/add": self._node_add, "/api/node/remove": self._node_remove,
                  "/api/node/purge": self._node_purge, "/api/autopurge": self._autopurge, "/api/node/restore-route": self._node_restore_route,
                  "/api/node/reset-path": self._node_reset, "/api/settings": self._save_settings,
                  "/api/radio": self._radio, "/api/privacy": self._privacy, "/api/node/reboot": self._reboot,
                  "/api/stats": lambda d: self.node.stats(),
                  "/api/channel/set": self._channel_set, "/api/channel/remove": self._channel_remove,
                  "/api/identity/export": lambda d: {"key": self.node.export_key()},
                  "/api/identity/import": lambda d: self.node.import_key(str(d["key"])),
                  "/api/contact/export": self._contact_export, "/api/contact/import": self._contact_import,
                  "/api/contact/share": lambda d: self.node.share_contact(self._key(d)),
                  "/api/admin/state": lambda d: self.node.remote.state(self._key(d)),
                  "/api/admin/login": lambda d: self.node.remote.login(self._key(d), str(d.get("password", ""))),
                  "/api/admin/logout": lambda d: self.node.remote.logout(self._key(d)),
                  "/api/admin/status": lambda d: {"status": self.node.remote.status(self._key(d))},
                  "/api/admin/neighbours": lambda d: {"neighbours": self.node.remote.neighbours(self._key(d))},
                  "/api/admin/telemetry": lambda d: {"telemetry": self.node.remote.telemetry(self._key(d))},
                  "/api/admin/acl": lambda d: {"acl": self.node.remote.acl(self._key(d))},
                  "/api/admin/route": lambda d: {"route": self.node.remote.route(self._key(d))},
                  "/api/admin/command": lambda d: {"reply": self.node.remote.command(self._key(d), str(d.get("text", "")))},
                  "/api/firmware/check": lambda d: {"release": self.node.firmware.check()},
                  "/api/firmware/flash": self._flash, "/api/firmware/upload": self._flash_upload}
        limits = {"/api/firmware/upload": UPLOAD_JSON_BYTES}
        self.private_posts = {path: self.json_route(fn, limits.get(path, 4096), (MeshError, FirmwareError))
                              for path, fn in routes.items()}

    def intro(self) -> list[dict]:
        return self.node.intro()

    def get(self, h, path: str) -> bool:
        if path == "/api/certificate" and self.cert_file and self.cert_file.is_file():
            h.reply(200, self.cert_file.read_text(), "application/x-x509-ca-cert",
                    headers=(("Content-Disposition", 'attachment; filename="simplemesh.crt"'),))
        elif path == "/api/settings":
            h.reply(200, self.settings_view())
        else:
            return False
        return True

    def settings_view(self) -> dict:
        home = self.cfg.home
        return {"values": {"serial_port": self.cfg.serial_port, "baud": self.cfg.baud, "nomad_url": self.cfg.nomad_url,
                           "auto_purge": self.cfg.auto_purge, "purge_high": self.cfg.purge_high, "purge_low": self.cfg.purge_low},
                "ports": all_ports(), "detected": candidate_ports(), "bauds": BAUDS, "map_server": self.proxy.nomad,
                "home": list(home) if home else None, "presets": PRESETS, "bandwidths": BANDWIDTHS}

    @staticmethod
    def _key(d: dict, pattern: re.Pattern = KEY, name: str = "key") -> str:
        v = str(d.get(name, "")).lower()
        if not pattern.match(v):
            raise ValueError(name)
        return v

    def _send(self, d: dict) -> dict:
        text = str(d.get("text", ""))
        if d.get("kind") == "chan":
            return {"msg": self.node.send_channel(int(d["chan"]), text)}
        if d.get("kind") == "dm":
            return {"msg": self.node.send_dm(self._key(d, PREFIX, "to"), text)}
        raise ValueError("kind")

    def _advert(self, d: dict) -> None:
        self.node.advertise(bool(d.get("flood")))

    def _profile(self, d: dict) -> None:
        self.node.set_profile(str(d["name"]), float(d["lat"]), float(d["lon"]))

    def _node_add(self, d: dict) -> None:
        self.node.add_node(self._key(d))

    def _node_remove(self, d: dict) -> None:
        self.node.remove_node(self._key(d), forget=bool(d.get("forget")))

    def _node_purge(self, d: dict) -> dict:
        keys = d.get("keys")
        if not isinstance(keys, list) or not all(isinstance(k, str) and KEY.match(k) for k in keys):
            raise ValueError("keys")
        return {"removed": self.node.purge_nodes(keys)}

    def _autopurge(self, d: dict) -> None:
        high, low = int(d["high"]), int(d["low"])
        if not 50 <= low < high <= 99:
            raise MeshError("Use percentages from 50 to 99, with the point where it stops below the point where it starts.")
        self.cfg.auto_purge, self.cfg.purge_high, self.cfg.purge_low = bool(d["enabled"]), high, low
        self.cfg.save()

    def _node_restore_route(self, d: dict) -> None:
        self.node.restore_route(self._key(d))

    def _node_reset(self, d: dict) -> None:
        self.node.reset_path(self._key(d))

    def _radio(self, d: dict) -> None:
        self.node.set_radio(float(d["freq"]), float(d["bw"]), int(d["sf"]), int(d["cr"]), int(d["tx_power"]))

    def _privacy(self, d: dict) -> None:
        self.node.set_privacy(bool(d["manual_add"]), int(d["tel_base"]), int(d["tel_loc"]), int(d["tel_env"]), int(d["loc_policy"]))

    def _reboot(self, d: dict) -> None:
        self.node.reboot()

    def _channel_set(self, d: dict) -> None:
        self.node.set_channel(int(d["idx"]), str(d["name"]), str(d.get("secret", "")))

    def _channel_remove(self, d: dict) -> None:
        self.node.remove_channel(int(d["idx"]))

    def _contact_export(self, d: dict) -> dict:
        return {"uri": self.node.export_contact(self._key(d) if d.get("key") else None)}

    def _contact_import(self, d: dict) -> None:
        self.node.import_contact(str(d["uri"]))

    def _flash(self, d: dict) -> None:
        self.node.firmware.start(image=str(d["image"]), erase=bool(d.get("erase")))

    def _flash_upload(self, d: dict) -> None:
        self.node.firmware.start(upload=(str(d.get("name", ""))[:80], str(d["data"])), erase=bool(d.get("erase")))

    def _save_settings(self, d: dict) -> None:
        port, baud, url = str(d.get("serial_port", "")).strip(), int(d.get("baud", 0)), str(d.get("nomad_url", "")).strip()
        if port and not port.startswith("/dev/"):
            raise MeshError("The serial port is a device path such as /dev/ttyACM0, or blank to find it automatically.")
        if baud not in BAUDS:
            raise MeshError("Pick one of the listed baud rates (the companion firmware uses 115200).")
        if url and not re.match(r"^https?://[^\s/]+", url):
            raise MeshError("The map server is a web address, like http://localhost:8080.")
        moved = (port, baud) != (self.cfg.serial_port, self.cfg.baud)
        self.cfg.serial_port, self.cfg.baud, self.cfg.nomad_url = port, baud, url
        self.cfg.save()
        self.proxy.nomad = url.rstrip("/")
        if moved:
            self.node.reconnect()
