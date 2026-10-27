"""simplemesh: changing the node (radio, channels, identity), reaching other nodes, and updating firmware."""
import base64
import hashlib
import json
import threading
import time

import pytest

pytest.importorskip("meshcore")

from meshcore import EventType  # noqa: E402

from simplemesh import remote  # noqa: E402
from simplemesh.demo import DEMO_IMAGE, ANNA_KEY, demo_fetch, demo_flasher, demo_release  # noqa: E402
from simplemesh.firmware import FirmwareError, RELEASES, version_tuple  # noqa: E402
from simplemesh.node import MeshError  # noqa: E402
from test_mesh import ANNA, HILL, ROOM, Rig, call, login, rig, until, web  # noqa: E402,F401


def sent(r, name):
    return [s for s in r.fake.commands.sent if s[0] == name]


# --- radio and privacy ----------------------------------------------------------------------------------

def test_radio_settings_reach_the_node_and_the_page(rig):
    rig.events()
    rig.node.set_radio(869.618, 62.5, 8, 8, 17)
    (_, freq, bw, sf, cr), = sent(rig, "set_radio")
    assert (int(freq * 1000), int(bw * 1000), sf, cr) == (869618, 62500, 8, 8)  # whole kHz, not one short
    assert sent(rig, "set_tx_power") == [("set_tx_power", 17)]
    me = rig.intro("self")
    assert (me["freq"], me["bw"], me["sf"], me["cr"], me["tx_power"]) == (869.618, 62.5, 8, 8, 17)
    assert any("self" in e and e["self"]["freq"] == 869.618 for e in rig.events())


def test_every_preset_survives_the_librarys_rounding(rig):
    from simplemesh.node import PRESETS
    for p in PRESETS:
        rig.node.set_radio(p["freq"], p["bw"], p["sf"], p["cr"], 10)
        assert rig.intro("self")["freq"] == p["freq"] and rig.intro("self")["bw"] == p["bw"], p


@pytest.mark.parametrize("args", [(100, 62.5, 7, 5, 10), (910.5, 63, 7, 5, 10), (910.5, 62.5, 13, 5, 10), (910.5, 62.5, 7, 9, 10),
                                  (910.5, 62.5, 7, 5, 0), (910.5, 62.5, 7, 5, 23)])
def test_nonsense_radio_settings_are_refused(rig, args):
    with pytest.raises(MeshError):
        rig.node.set_radio(*args)
    assert not sent(rig, "set_radio")


def test_a_radio_the_node_refuses_is_reported(rig):
    rig.fake.commands.refuse.add("set_radio")
    with pytest.raises(MeshError, match="refused"):
        rig.node.set_radio(910.525, 62.5, 7, 5, 10)


def test_privacy_settings_keep_everything_else(rig):
    rig.node.set_privacy(True, 1, 2, 0, 0)
    (_, infos), = sent(rig, "set_other_params")
    assert infos["multi_acks"] == 0 and infos["radio_freq"] == 910.525  # what the node already had went back unchanged
    me = rig.intro("self")
    assert (me["manual_add"], me["tel_base"], me["tel_loc"], me["tel_env"], me["loc_policy"]) == (True, 1, 2, 0, 0)
    with pytest.raises(MeshError):
        rig.node.set_privacy(False, 3, 0, 0, 0)


# --- channels -------------------------------------------------------------------------------------------

def chan(rig, idx):
    return next((c for c in rig.intro("channels") if c["idx"] == idx), None)


def test_a_hashtag_channel_takes_its_key_from_its_name(rig):
    rig.node.set_channel(2, "#weather")
    assert chan(rig, 2)["name"] == "#weather"
    assert chan(rig, 2)["secret"] == hashlib.sha256(b"#weather").digest()[:16].hex()


def test_a_private_channel_gets_a_random_key_and_keeps_it(rig):
    rig.node.set_channel(3, "family")
    key = chan(rig, 3)["secret"]
    assert len(key) == 32 and key != "00" * 16
    rig.node.set_channel(3, "family 2")  # renaming must not change the key
    assert chan(rig, 3)["name"] == "family 2" and chan(rig, 3)["secret"] == key


def test_joining_a_channel_with_its_secret(rig):
    rig.node.set_channel(2, "ranch", "AB" * 16)
    assert chan(rig, 2)["secret"] == "ab" * 16
    for bad in ("zz" * 16, "ab" * 15):
        with pytest.raises(MeshError, match="32 hex"):
            rig.node.set_channel(2, "ranch", bad)


def test_channel_names_and_slots_are_checked(rig):
    for name, idx in (("", 2), ("x" * 32, 2), ("bad\nname", 2), ("ok", 99), ("ok", -1)):
        with pytest.raises(MeshError):
            rig.node.set_channel(idx, name)
    assert not sent(rig, "set_channel")


def test_channels_can_be_removed_except_public(rig):
    rig.node.remove_channel(1)
    assert chan(rig, 1) is None and 1 not in rig.fake.channels
    with pytest.raises(MeshError, match="Public"):
        rig.node.remove_channel(0)
    with pytest.raises(MeshError, match="No such"):
        rig.node.remove_channel(5)


def test_a_channel_the_node_refuses_is_not_listed(rig):
    rig.fake.commands.refuse.add("set_channel")
    with pytest.raises(MeshError, match="refused"):
        rig.node.set_channel(2, "#nope")
    assert chan(rig, 2) is None


# --- the node itself ------------------------------------------------------------------------------------

def test_reboot_asks_the_node_and_reconnects(rig):
    rig.node.reboot()
    assert sent(rig, "reboot")
    until(lambda: rig.node._status["connected"] and rig.fake.disconnected, "a fresh connection")


def test_the_key_can_be_exported_and_imported(rig):
    assert rig.node.export_key() == bytes(range(64)).hex()
    rig.fake.commands.refuse.add("export_key")
    with pytest.raises(MeshError, match="turned off"):
        rig.node.export_key()
    for bad in ("", "ab" * 63, "zz" * 64):
        with pytest.raises(MeshError):
            rig.node.import_key(bad)
    assert not sent(rig, "import_key")
    rig.node.import_key(("AB" * 64))
    assert sent(rig, "import_key") == [("import_key", bytes.fromhex("ab" * 64))] and sent(rig, "reboot")


def test_statistics(rig):
    s = rig.node.stats()
    assert s["core"]["battery_mv"] == 3900 and s["radio"]["noise_floor"] == -112 and s["packets"]["recv"] == 120


def test_contact_links(rig):
    assert rig.node.export_contact(None).startswith("meshcore://")
    assert rig.node.export_contact(ANNA["public_key"]).startswith("meshcore://")
    with pytest.raises(MeshError, match="saved"):
        rig.node.export_contact("ab" * 32)
    rig.node.import_contact(" meshcore://" + "ab" * 40 + " ")
    assert sent(rig, "import_contact") == [("import_contact", bytes.fromhex("ab" * 40))]
    for bad in ("hello", "meshcore://", "meshcore://xyz", "http://" + "ab" * 40, "meshcore://" + "a" * 21):
        with pytest.raises(MeshError):
            rig.node.import_contact(bad)
    rig.node.share_contact(ANNA["public_key"])
    assert sent(rig, "share_contact")
    with pytest.raises(MeshError):
        rig.node.share_contact("ab" * 32)


# --- other nodes ----------------------------------------------------------------------------------------

def test_logging_in_to_a_repeater(rig):
    assert rig.node.remote.state(HILL["public_key"]) == {"logged_in": False, "admin": False}
    with pytest.raises(MeshError, match="No login answer"):
        rig.node.remote.login(HILL["public_key"], "wrong")
    assert rig.node.remote.login(HILL["public_key"], "hunter2") == {"logged_in": True, "admin": True}
    assert rig.node.remote.state(HILL["public_key"])["logged_in"]
    rig.node.remote.logout(HILL["public_key"])
    assert not rig.node.remote.state(HILL["public_key"])["logged_in"]


def test_only_saved_repeaters_rooms_and_sensors_can_be_managed(rig):
    for key, why in ((ANNA["public_key"], "repeaters, room"), ("ab" * 32, "Save that")):
        with pytest.raises(MeshError, match=why):
            rig.node.remote.login(key, "x")
    assert rig.node.remote.login(ROOM["public_key"], "hunter2")["logged_in"]


def test_asking_a_repeater_how_it_is(rig):
    key = HILL["public_key"]
    assert rig.node.remote.status(key)["uptime"] == 90061
    assert rig.node.remote.neighbours(key)["neighbours"][0]["snr"] == 8.5
    assert rig.node.remote.telemetry(key)[0]["type"] == "voltage"
    assert rig.node.remote.acl(key)[0]["perm"] == 1
    assert rig.node.remote.route(ANNA["public_key"])["out_path"] == "ab"  # any saved node, not just repeaters


def test_a_silent_node_gives_an_explanation_not_a_hang(rig):
    rig.fake.commands.silent = True
    for call_ in (rig.node.remote.status, rig.node.remote.neighbours, rig.node.remote.telemetry, rig.node.remote.acl,
                  rig.node.remote.route):
        with pytest.raises(MeshError, match="No (answer|route)"):
            call_(HILL["public_key"])


def test_a_repeaters_command_line(rig):
    assert rig.node.remote.command(HILL["public_key"], "ver") == "MeshCore v1.18.0 (demo)"
    assert ("cmd", HILL["public_key"], "ver") in rig.fake.commands.sent
    assert rig.store.recent_messages() == []  # replies are answers, not conversation
    with pytest.raises(MeshError, match="first"):
        rig.node.remote.command(HILL["public_key"], "  ")
    with pytest.raises(MeshError, match="too long"):
        rig.node.remote.command(HILL["public_key"], "x" * 141)


def test_a_command_with_no_answer_times_out_politely(rig, monkeypatch):
    monkeypatch.setattr(remote, "REPLY_WAIT_S", 0.2)
    with pytest.raises(MeshError, match="No reply"):
        rig.node.remote.command(HILL["public_key"], "reboot")  # the fake only answers what it has an entry for
    assert rig.node.remote.command(HILL["public_key"], "clock").startswith("12:00")  # and the next one still works


def test_a_reply_goes_to_the_command_that_asked(rig):
    rig.fake.commands.replies["a"], rig.fake.commands.replies["b"] = "answer a", "answer b"
    out = {}
    ts = [threading.Thread(target=lambda c=c: out.__setitem__(c, rig.node.remote.command(HILL["public_key"], c))) for c in "ab"]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == {"a": "answer a", "b": "answer b"}


def test_a_room_can_be_posted_to(rig):
    msg = rig.node.send_dm(ROOM["public_key"][:12], "hello room")
    until(lambda: ("dm", ROOM["public_key"], "hello room") in rig.fake.commands.sent, "the post")
    assert msg["peer"] == ROOM["public_key"][:12]
    with pytest.raises(MeshError, match="companions and room"):
        rig.node.send_dm(HILL["public_key"][:12], "hello repeater")


# --- the serial port is shared with the firmware updater -----------------------------------------------

def test_pausing_lets_go_of_the_port_and_resuming_takes_it_back(rig):
    rig.node.pause()
    assert rig.node.mc is None and rig.fake.disconnected and not rig.node._status["connected"]
    assert "Paused" in rig.node._status["text"]
    time.sleep(0.3)  # several retry periods: it must stay away
    assert rig.node.mc is None
    rig.fake.disconnected = False
    rig.node.resume()
    until(lambda: rig.node._status["connected"], "the node to reconnect")


def test_a_pause_while_the_node_is_not_connected_is_fine(tmp_path):
    r = Rig(tmp_path, connector=None, ports=lambda: []).start()
    try:
        until(lambda: "No USB" in r.node._status["text"], "the search to start")
        r.node.pause()
        r.node.resume()
    finally:
        r.node.stop()


# --- firmware -------------------------------------------------------------------------------------------

class Flasher:
    """Stands in for esptool: records its commands and what the node was doing at the time."""
    def __init__(self, rig_, rc=0, lines=("Connected to ESP32-S3", "Writing at 0x00001000 50.0% ", "Hash of data verified.")):
        self.rig, self.rc, self.lines, self.cmds, self.node_was_free = rig_, rc, lines, [], []

    def __call__(self, cmd, on_line):
        self.cmds.append(cmd)
        self.node_was_free.append(self.rig.node.mc is None)
        for line in self.lines:
            on_line(line)
        return self.rc


def finished(r):
    return until(lambda: r.node.firmware.state()["state"] in ("done", "failed") and r.node.firmware.state(), "the update to end")


def test_checking_for_firmware(tmp_path):
    r = Rig(tmp_path, fetch=demo_fetch, flasher=Flasher(None)).connect()
    try:
        r.node._self["model"] = "Heltec Wireless Paper"
        r.node._self["version"] = "v1.17.1-abcdef"
        rel = r.node.firmware.check()
        assert rel["version"] == "v1.18.0" and rel["newer"] is True and rel["tag"] == "companion-v1.18.0"
        assert rel["suggested"] == "Heltec_Wireless_Paper_companion_radio_usb-v1.18.0-81dbae5-merged.bin"
        names = [i["name"] for i in rel["images"]]
        assert len(names) == 5 and all("_usb-" in n and n.endswith("-merged.bin") for n in names)  # no BLE builds
        r.node._self["version"] = "v1.18.0-81dbae5"
        assert r.node.firmware.check()["newer"] is False
        r.node._self["version"] = ""
        assert r.node.firmware.check()["newer"] is None
    finally:
        r.node.stop()


def test_versions_compare_as_numbers():
    assert version_tuple("v1.18.0-81dbae5") > version_tuple("v1.9.2") > version_tuple("v1.9")
    assert version_tuple("") == ()


def test_checking_without_internet_says_so(tmp_path):
    def offline(url):
        raise OSError("Network is unreachable")
    r = Rig(tmp_path, fetch=offline).connect()
    try:
        with pytest.raises(FirmwareError, match="pick a firmware file"):
            r.node.firmware.check()
    finally:
        r.node.stop()


def test_a_release_with_nothing_for_us(tmp_path):
    r = Rig(tmp_path, fetch=lambda url: json.dumps([{"tag_name": "repeater-v1.0.0"}]).encode()).connect()
    try:
        with pytest.raises(FirmwareError, match="No companion"):
            r.node.firmware.check()
    finally:
        r.node.stop()


def test_only_asset_urls_on_the_projects_releases_are_offered(tmp_path):
    evil = json.dumps([{"tag_name": "companion-v9.9.9", "assets": [
        {"name": "Evil_companion_radio_usb-v9.9.9-abc1234-merged.bin", "browser_download_url": "http://evil.example/x.bin", "size": 1}]}])
    r = Rig(tmp_path, fetch=lambda url: evil.encode()).connect()
    try:
        assert r.node.firmware.check()["images"] == []
    finally:
        r.node.stop()


def update(tmp_path, flasher_rc=0, fetch=demo_fetch, **kw):
    r = Rig(tmp_path, fetch=fetch, **kw)
    r.flasher = Flasher(r, flasher_rc)
    r.node.firmware.flasher = r.flasher
    r.connect()
    r.node._self["model"] = "Heltec Wireless Paper"
    rel = r.node.firmware.check()
    return r, rel["suggested"]


def test_updating_to_a_release(tmp_path):
    r, image = update(tmp_path)
    try:
        r.events()
        r.node.firmware.start(image=image)
        st = finished(r)
        assert st["state"] == "done" and st["percent"] == 100, st
        cmd, = r.flasher.cmds
        assert cmd[:2] == ["--port", "fake"] and cmd[2:6] == ["--baud", "460800", "write-flash", "0x0"] and cmd[6].endswith("firmware.bin")
        assert r.flasher.node_was_free == [True]  # the node let go of the port first
        until(lambda: r.node._status["connected"], "the node to come back")
        flashes = [e["flash"] for e in r.events() if "flash" in e]
        assert [f["state"] for f in flashes][0] == "starting" and flashes[-1]["state"] == "done"
        assert 50 in [f["percent"] for f in flashes] and "Hash of data verified." in flashes[-1]["log"]
        assert any(e.get("flash", {}).get("state") == "done" for e in r.node.intro())  # a new page sees it too
    finally:
        r.node.stop()


def test_erasing_first(tmp_path):
    r, image = update(tmp_path)
    try:
        r.node.firmware.start(image=image, erase=True)
        assert finished(r)["state"] == "done"
        assert [c[2] for c in r.flasher.cmds] == ["erase-flash", "--baud"]
    finally:
        r.node.stop()


def test_a_failed_flash_is_explained_and_the_node_comes_back(tmp_path):
    r, image = update(tmp_path, flasher_rc=2)
    try:
        r.flasher.lines = ("A fatal error occurred: Failed to connect to ESP32-S3: No serial data received.",)
        r.node.firmware.start(image=image)
        st = finished(r)
        assert st["state"] == "failed" and "Failed to connect" in st["text"] and "BOOT" in st["text"]
        until(lambda: r.node._status["connected"], "the node to be picked up again")
        assert not r.node._held
    finally:
        r.node.stop()


def test_an_uploaded_file(tmp_path):
    r, _ = update(tmp_path)
    try:
        r.node.firmware.start(upload=("mine.bin", base64.b64encode(DEMO_IMAGE).decode()))
        assert finished(r)["state"] == "done" and "mine.bin" in r.node.firmware.state()["image"]
        assert len(r.flasher.cmds) == 1
    finally:
        r.node.stop()


@pytest.mark.parametrize("data,why", [(b"", "size"), (b"\xe9" * 10, "size"), (b"MZ" + bytes(100_000), "ESP32"),
                                      (b"\xe9" + bytes(5 * 1024 * 1024), "size")])
def test_files_that_are_not_firmware_are_refused(tmp_path, data, why):
    r, _ = update(tmp_path)
    try:
        with pytest.raises(FirmwareError, match=why):
            r.node.firmware.start(upload=("x.bin", base64.b64encode(data).decode()))
        assert r.node.firmware.state()["state"] == "idle" and not r.flasher.cmds
        with pytest.raises(FirmwareError, match="intact"):
            r.node.firmware.start(upload=("x.bin", "not base64!!"))
    finally:
        r.node.stop()


def test_only_listed_images_can_be_flashed(tmp_path):
    r = Rig(tmp_path, fetch=demo_fetch, flasher=Flasher(None)).connect()
    try:
        with pytest.raises(FirmwareError, match="Check for updates"):
            r.node.firmware.start(image="anything.bin")  # nothing checked yet
        r.node.firmware.check()
        for name in ("../../etc/passwd", "Evil.bin", ""):
            with pytest.raises(FirmwareError):
                r.node.firmware.start(image=name)
    finally:
        r.node.stop()


def test_a_damaged_download_is_not_flashed(tmp_path):
    def fetch(url):
        return demo_fetch(url) if url == RELEASES else bytes([0xE9]) + bytes(100_000)[:-1] + b"\x01"  # right size, wrong content
    r, image = update(tmp_path, fetch=fetch)
    try:
        r.node.firmware.start(image=image)
        st = finished(r)
        assert st["state"] == "failed" and "checksum" in st["text"] and not r.flasher.cmds
    finally:
        r.node.stop()


def test_a_short_download_is_not_flashed(tmp_path):
    r, image = update(tmp_path, fetch=lambda url: demo_fetch(url) if url == RELEASES else DEMO_IMAGE[:5000])
    try:
        r.node.firmware.start(image=image)
        assert "wrong size" in finished(r)["text"] and not r.flasher.cmds
    finally:
        r.node.stop()


def test_a_failed_download_is_reported(tmp_path):
    def fetch(url):
        if url == RELEASES:
            return demo_release()
        raise OSError("connection reset")
    r, image = update(tmp_path, fetch=fetch)
    try:
        r.node.firmware.start(image=image)
        assert "download failed" in finished(r)["text"]
        assert r.node._status["connected"]  # never touched the node
    finally:
        r.node.stop()


def test_one_update_at_a_time(tmp_path):
    gate = threading.Event()

    def slow(cmd, on_line):
        gate.wait(5)
        return 0
    r, image = update(tmp_path)
    r.node.firmware.flasher = slow
    try:
        r.node.firmware.start(image=image)
        until(lambda: r.node.firmware.state()["state"] == "flashing", "flashing to begin")
        with pytest.raises(FirmwareError, match="already"):
            r.node.firmware.start(image=image)
        gate.set()
        assert finished(r)["state"] == "done"
    finally:
        gate.set()
        r.node.stop()


def test_updating_with_no_node_plugged_in(tmp_path):
    """The case that matters most: a node whose firmware isn't answering (so there is no connection) still updates."""
    calls = []

    async def silent(port, baud):
        calls.append(port)
        return None
    r = Rig(tmp_path, connector=silent, fetch=demo_fetch)
    r.flasher = Flasher(r)
    r.node.firmware.flasher = r.flasher
    r.start()
    try:
        until(lambda: calls, "a connection attempt")
        rel = r.node.firmware.check()
        r.node.firmware.start(image=rel["images"][0]["name"])
        assert finished(r)["state"] == "done" and r.flasher.cmds[0][1] == "fake"
    finally:
        r.node.stop()


# --- over HTTP ------------------------------------------------------------------------------------------

def test_the_new_requests_need_a_login(web):
    for path in ("/api/radio", "/api/privacy", "/api/channel/set", "/api/channel/remove", "/api/node/reboot", "/api/stats",
                 "/api/identity/export", "/api/identity/import", "/api/contact/export", "/api/contact/import",
                 "/api/contact/share", "/api/admin/login", "/api/admin/command", "/api/admin/status", "/api/firmware/check",
                 "/api/firmware/flash", "/api/firmware/upload"):
        assert call(web, "POST", path, {})[0] == 401, path
    assert not web.fake.commands.sent.count(("reboot",))


def test_radio_and_channels_over_http(web):
    cookie = login(web)
    body = {"freq": 910.525, "bw": 62.5, "sf": 7, "cr": 5, "tx_power": 15}
    assert call(web, "POST", "/api/radio", body, cookie)[0] == 200
    assert web.intro("self")["tx_power"] == 15
    st, data, _ = call(web, "POST", "/api/radio", {**body, "bw": 3}, cookie)
    assert st == 409 and "bandwidth" in data["error"]
    assert call(web, "POST", "/api/radio", {"freq": "fast"}, cookie)[0] == 400
    assert call(web, "POST", "/api/channel/set", {"idx": 2, "name": "#chat"}, cookie)[0] == 200
    assert call(web, "POST", "/api/channel/remove", {"idx": 2}, cookie)[0] == 200
    assert call(web, "POST", "/api/channel/remove", {"idx": 0}, cookie)[0] == 409
    assert call(web, "POST", "/api/channel/set", {"name": "x"}, cookie)[0] == 400
    settings = call(web, "GET", "/api/settings", cookie=cookie)[1]
    assert settings["presets"][0]["name"] == "USA / Canada" and 62.5 in settings["bandwidths"]


def test_managing_a_repeater_over_http(web):
    cookie = login(web)
    key = HILL["public_key"]
    assert call(web, "POST", "/api/admin/login", {"key": key, "password": "nope"}, cookie)[0] == 409
    st, data, _ = call(web, "POST", "/api/admin/login", {"key": key, "password": "hunter2"}, cookie)
    assert st == 200 and data["admin"] is True
    assert call(web, "POST", "/api/admin/state", {"key": key}, cookie)[1]["logged_in"] is True
    assert call(web, "POST", "/api/admin/status", {"key": key}, cookie)[1]["status"]["bat"] == 4012
    assert call(web, "POST", "/api/admin/command", {"key": key, "text": "ver"}, cookie)[1]["reply"].startswith("MeshCore")
    assert call(web, "POST", "/api/admin/neighbours", {"key": key}, cookie)[1]["neighbours"]["neighbours"]
    assert call(web, "POST", "/api/admin/command", {"key": "short", "text": "ver"}, cookie)[0] == 400
    assert call(web, "POST", "/api/admin/command", {"key": ANNA["public_key"], "text": "ver"}, cookie)[0] == 409
    assert "hunter2" not in json.dumps(web.store.messages) and "hunter2" not in json.dumps(web.intro("nodes"))


def test_the_identity_export_is_only_for_the_logged_in(web):
    assert call(web, "POST", "/api/identity/export", {})[0] == 401
    cookie = login(web)
    assert call(web, "POST", "/api/identity/export", {}, cookie)[1]["key"] == bytes(range(64)).hex()
    assert call(web, "POST", "/api/identity/import", {"key": "nothex"}, cookie)[0] == 409


def test_firmware_over_http(web):
    cookie = login(web)
    web.node.firmware.fetch = demo_fetch
    web.node.firmware.flasher = Flasher(web)
    web.node._self["model"] = "Heltec Wireless Paper"
    st, data, _ = call(web, "POST", "/api/firmware/check", {}, cookie)
    assert st == 200 and data["release"]["suggested"]
    assert call(web, "POST", "/api/firmware/flash", {"image": "nope.bin"}, cookie)[0] == 409
    assert call(web, "POST", "/api/firmware/flash", {}, cookie)[0] == 400
    up = {"name": "mine.bin", "data": base64.b64encode(b"\xe9" + bytes(200_000)).decode()}  # bigger than the default body limit
    assert call(web, "POST", "/api/firmware/upload", up, cookie)[0] == 200
    assert finished(web)["state"] == "done"
    bad = {"name": "x", "data": base64.b64encode(b"MZ" + bytes(100_000)).decode()}
    st, data, _ = call(web, "POST", "/api/firmware/upload", bad, cookie)
    assert st == 409 and "ESP32" in data["error"]
    huge = {"name": "x", "data": "A" * (7 * 1024 * 1024)}
    try:
        assert call(web, "POST", "/api/firmware/upload", huge, cookie)[0] == 400  # past even the upload limit
    except (BrokenPipeError, ConnectionResetError):
        pass  # the server hung up before the whole body was sent, which is just as good
    assert web.node.firmware.state()["state"] == "done"


def test_downloads_are_capped(monkeypatch):
    from simplemesh import firmware

    class Reply:
        def __init__(self, n):
            self.n = n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, limit):
            return bytes(min(self.n, limit))
    monkeypatch.setattr(firmware.urllib.request, "urlopen", lambda req, timeout: Reply(firmware.MAX_BYTES + 5))
    with pytest.raises(OSError, match="larger"):
        firmware.http_get(firmware.DOWNLOADS + "x.bin")
    monkeypatch.setattr(firmware.urllib.request, "urlopen", lambda req, timeout: Reply(firmware.MAX_BYTES + 5))
    assert len(firmware.http_get(firmware.RELEASES)) == firmware.MAX_BYTES + 5  # the release list may be bigger than an image
