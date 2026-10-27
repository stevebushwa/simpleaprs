"""simpleaprs main window."""
from __future__ import annotations

import json
import os
import random
import sys
import time

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox, QSizePolicy, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from piwebkit.hub import Hub
from piwebkit.server import lan_ip, new_token
from piwebkit.tilecache import TileCache

from . import aprs, icons, trails
from .alerts import Alerts
from .audio import Radio, device_names
from .ax25 import Frame
from .config import STATIONS_PATH, STATIONS_REMOTE_PATH, Config
from .digipeater import Digipeater
from .igate import IGate, passcode
from .mapserver import MapServer, tile_cache
from .netclient import RemoteClient
from .netshare import ShareServer


MESSAGE_FILTERS = [("All messages", "all"), ("Direct messages", "direct"), ("Bulletins", "bulletin")]
FILTERS = [("All stations", "all"), ("Weather stations", "weather"), ("iGates", "igate"),
           ("Digipeaters", "digi"), ("Mobile (vehicles, aircraft, boats)", "mobile")]


class MapPage(QWebEnginePage):
    """The map's popup buttons ask for things by navigating to simpleaprs://<what>/?call=<station>.
    That is caught here instead of loaded."""
    station_action = Signal(str, str)  # what, station

    def acceptNavigationRequest(self, url, _type, _is_main_frame) -> bool:
        if url.scheme() != "simpleaprs":
            return True
        self.station_action.emit(url.host(), QUrlQuery(url).queryItemValue("call"))
        return False


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.cfg = cfg
        self.call = QLineEdit(cfg.callsign)
        self.call.setPlaceholderText("e.g. N0CALL-7")
        self.lat = QDoubleSpinBox(); self.lat.setRange(-90, 90); self.lat.setDecimals(5); self.lat.setValue(cfg.lat)
        self.lon = QDoubleSpinBox(); self.lon.setRange(-180, 180); self.lon.setDecimals(5); self.lon.setValue(cfg.lon)
        self.symbol = QComboBox()
        for label, sym in aprs.SYMBOLS:
            self.symbol.addItem(f"{label}  ({sym})", sym)
        self.symbol.addItem("Custom...", None)
        self.custom = QLineEdit(); self.custom.setMaxLength(2); self.custom.setMaximumWidth(60)
        self.custom.setPlaceholderText("/-")
        self.preview = QLabel(); self.preview.setFixedSize(44, 44)
        known = self.symbol.findData(cfg.symbol)
        self.symbol.setCurrentIndex(known if known >= 0 else self.symbol.count() - 1)
        if known < 0:
            self.custom.setText(cfg.symbol)
        self.symbol.currentIndexChanged.connect(self._symbol_changed)
        self.custom.textChanged.connect(self._symbol_changed)
        symbol_row = QWidget(); sr = QHBoxLayout(symbol_row); sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(self.preview); sr.addWidget(self.symbol, 1); sr.addWidget(self.custom)
        self._symbol_changed()
        self.comment = QLineEdit(cfg.comment)
        self.path = QLineEdit(cfg.path)
        self.inp = QComboBox(); self.inp.addItem("(system default)", ""); [self.inp.addItem(n, n) for n in device_names(True)]
        self.out = QComboBox(); self.out.addItem("(system default)", ""); [self.out.addItem(n, n) for n in device_names(False)]
        self.inp.setCurrentIndex(max(0, self.inp.findData(cfg.input_device)))
        self.out.setCurrentIndex(max(0, self.out.findData(cfg.output_device)))
        self.lead = QSpinBox(); self.lead.setRange(100, 3000); self.lead.setSuffix(" ms"); self.lead.setValue(cfg.tx_lead_ms)
        self.auto_gain = QCheckBox("Adjust the input volume automatically"); self.auto_gain.setChecked(cfg.auto_gain)
        self.beacon = QSpinBox(); self.beacon.setRange(0, 120); self.beacon.setSuffix(" min (0 = manual)"); self.beacon.setValue(cfg.beacon_minutes)
        self.nomad = QLineEdit(cfg.nomad_url)
        self.cache_on = QCheckBox("Cache map tiles on this computer"); self.cache_on.setChecked(cfg.map_cache)
        self.cache_mb = QSpinBox(); self.cache_mb.setRange(50, 20000); self.cache_mb.setSuffix(" MB"); self.cache_mb.setSingleStep(100)
        self.cache_mb.setValue(cfg.map_cache_mb)
        self.cache_info = QLabel()
        clear = QPushButton("Clear cache"); clear.clicked.connect(self._clear_cache)
        cache_row = QWidget(); cr = QHBoxLayout(cache_row); cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(self.cache_mb); cr.addWidget(self.cache_info, 1); cr.addWidget(clear)
        self.cache_on.toggled.connect(lambda on: (self.cache_mb.setEnabled(on)))
        self.cache_mb.setEnabled(cfg.map_cache)
        self._cache_usage()
        self.alert_out = QComboBox(); self.alert_out.addItem("(system default)", ""); [self.alert_out.addItem(n, n) for n in device_names(False)]
        self.alert_out.setCurrentIndex(max(0, self.alert_out.findData(cfg.alert_device)))
        self.a_msg = QCheckBox("Direct message received"); self.a_msg.setChecked(cfg.alert_message)
        self.a_new = QCheckBox("New station heard"); self.a_new.setChecked(cfg.alert_new_station)
        self.a_sent = QCheckBox("Packet transmitted"); self.a_sent.setChecked(cfg.alert_sent)
        self.mode = QComboBox()
        self.mode.addItem("Use a radio on this computer", "local")
        self.mode.addItem("Connect to another simpleaprs", "remote")
        self.mode.setCurrentIndex(max(0, self.mode.findData(cfg.mode)))
        self.remote_url = QLineEdit(cfg.remote_url); self.remote_url.setPlaceholderText("raspberrypi.local:8073")
        self.remote_token = QLineEdit(cfg.remote_token); self.remote_token.setPlaceholderText("access token from the server")
        self.share = QCheckBox("Share this radio on the network"); self.share.setChecked(cfg.share_enabled)
        self.share_port = QSpinBox(); self.share_port.setRange(1024, 65535); self.share_port.setValue(cfg.share_port)
        self.share_token = QLineEdit(cfg.share_token)
        new = QPushButton("New"); new.clicked.connect(lambda: self.share_token.setText(new_token()))
        token_row = QWidget(); tr = QHBoxLayout(token_row); tr.setContentsMargins(0, 0, 0, 0); tr.addWidget(self.share_token, 1); tr.addWidget(new)
        self.share_require = QCheckBox("Require the token to watch (turn off to let anyone on the network watch)")
        self.share_require.setChecked(cfg.share_require_token)
        self.share_tx = QCheckBox("Let clients transmit through this radio (always needs the token)")
        self.share_tx.setChecked(cfg.share_allow_tx)
        self.ig_on = QCheckBox("Gate what the radio hears to the internet (APRS-IS)"); self.ig_on.setChecked(cfg.igate_enabled)
        self.ig_server = QLineEdit(cfg.igate_server); self.ig_server.setPlaceholderText("rotate.aprs2.net:14580")
        self.ig_pass = QLineEdit(cfg.igate_passcode); self.ig_pass.setPlaceholderText("APRS-IS passcode for your callsign")
        calc = QPushButton("Calculate"); calc.clicked.connect(self._calc_passcode)
        pass_row = QWidget(); pr = QHBoxLayout(pass_row); pr.setContentsMargins(0, 0, 0, 0); pr.addWidget(self.ig_pass, 1); pr.addWidget(calc)
        self.ig_to_rf = QCheckBox("Also transmit internet messages for stations heard directly here (uses your radio)")
        self.ig_to_rf.setChecked(cfg.igate_to_rf)
        self.ig_radius = QSpinBox(); self.ig_radius.setRange(1, 500); self.ig_radius.setSuffix(" km"); self.ig_radius.setValue(cfg.igate_radius_km)
        self.dg_on = QCheckBox("Repeat packets that ask for it"); self.dg_on.setChecked(cfg.digi_enabled)
        self.dg_mode = QComboBox()
        self.dg_mode.addItem("Fill-in: answers WIDE1-1 and our callsign", False)
        self.dg_mode.addItem("Wide-area: also answers WIDE2-1 and WIDE2-2", True)
        self.dg_mode.setCurrentIndex(1 if cfg.digi_wide else 0)
        for w in (self.mode,):
            w.currentIndexChanged.connect(self._mode_changed)
        self.share.toggled.connect(self._mode_changed)
        self.ig_on.toggled.connect(self._mode_changed); self.ig_to_rf.toggled.connect(self._mode_changed)
        self.dg_on.toggled.connect(self._mode_changed)
        self.share_hint = QLabel(); self.share_hint.setWordWrap(True)
        self._mode_changed()
        def page(rows, *notes: QLabel) -> QWidget:
            w = QWidget(); f = QFormLayout(w)
            for label, widget in rows:
                f.addRow(label, widget)
            for n in notes:
                n.setWordWrap(True); f.addRow(n)
            return w
        ig_hint = QLabel("Needs a callsign and the APRS-IS passcode for it. Packets that already came from the internet, "
                         "or ask not to be gated, are never passed on. Sending to the radio is limited to messages for stations "
                         "this radio has heard directly in the last 30 minutes, at most 10 a minute.")
        dg_hint = QLabel("This transmits on its own whenever a packet asks to be repeated, so it only helps if your radio "
                         "reaches ground other digipeaters don't. Leave it off if you're not in a good spot.")
        tabs = QTabWidget()
        tabs.addTab(page([("Callsign", self.call), ("Latitude", self.lat), ("Longitude", self.lon), ("Map symbol", symbol_row),
                          ("Beacon comment", self.comment), ("Path for your packets", self.path), ("Auto beacon", self.beacon)]), "Station")
        tabs.addTab(page([("Radio audio in", self.inp), ("Radio audio out", self.out), ("VOX lead-in", self.lead), ("Receive gain", self.auto_gain)],
                         QLabel("Tip: with VOX, raise the lead-in until the start of packets stops getting cut off."),
                         QLabel("Automatic input volume turns the sound card's capture volume down when the radio's audio clips and up "
                                "when it stays weak. Needs PulseAudio or PipeWire (pactl or wpctl).")), "Radio")
        tabs.addTab(page([("Alert sounds out", self.alert_out), ("Play alert for", self.a_msg), ("", self.a_new), ("", self.a_sent)]), "Alerts")
        tabs.addTab(page([("N.O.M.A.D. server", self.nomad), ("Map cache", self.cache_on), ("Cache size limit", cache_row)]), "Map")
        tabs.addTab(page([("Mode", self.mode), ("Server address", self.remote_url), ("Server token", self.remote_token),
                          ("Sharing", self.share), ("Share port", self.share_port), ("Share token", token_row),
                          ("", self.share_require), ("", self.share_tx)], self.share_hint), "Network")
        tabs.addTab(page([("", self.ig_on), ("Server", self.ig_server), ("Passcode", pass_row), ("", self.ig_to_rf),
                          ("Message range", self.ig_radius)], ig_hint), "iGate")
        tabs.addTab(page([("", self.dg_on), ("Kind", self.dg_mode)], dg_hint), "Digipeater")
        self.tabs = tabs
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        root = QVBoxLayout(self); root.addWidget(tabs); root.addWidget(bb)

    def _tile_cache(self) -> TileCache | None:
        return getattr(self.parent(), "tile_cache", None)

    def _cache_usage(self) -> None:
        c = self._tile_cache()
        self.cache_info.setText(f"{c.size / 1e6:.0f} MB used" if c else "")

    def _clear_cache(self) -> None:
        if self._tile_cache():
            self._tile_cache().clear()
        self._cache_usage()

    def _mode_changed(self) -> None:
        remote = self.mode.currentData() == "remote"
        for w in (self.remote_url, self.remote_token):
            w.setEnabled(remote)
        for w in (self.share, self.call, self.lat, self.lon, self.symbol, self.comment, self.path, self.inp, self.out, self.lead, self.beacon,
                  self.ig_on, self.dg_on):
            w.setEnabled(not remote)
        gating = self.ig_on.isChecked() and not remote
        for w in (self.ig_server, self.ig_pass, self.ig_to_rf):
            w.setEnabled(gating)
        self.ig_radius.setEnabled(gating and self.ig_to_rf.isChecked())
        self.dg_mode.setEnabled(self.dg_on.isChecked() and not remote)
        sharing = self.share.isChecked() and not remote
        for w in (self.share_port, self.share_token, self.share_require, self.share_tx):
            w.setEnabled(sharing)
        if not self.share_token.text():
            self.share_token.setText(new_token())
        self.share_hint.setText(
            "Other simpleaprs instances connect to http://%s:%d with the token. Viewers see your callsign, position and "
            "everything this radio hears. Traffic is not encrypted: keep it to your LAN or a VPN such as Tailscale." % (lan_ip(), self.share_port.value())
            if sharing else "")

    def _calc_passcode(self) -> None:
        call = self.call.text().strip()
        if call:
            self.ig_pass.setText(str(passcode(call)))
        else:
            self.tabs.setCurrentIndex(0)
            QMessageBox.warning(self, "Settings", "Enter your callsign first (Station tab).")

    def _symbol(self) -> str:
        sym = self.symbol.currentData()
        return sym if sym else self.custom.text()

    def _symbol_changed(self) -> None:
        self.custom.setEnabled(self.symbol.currentData() is None)
        sym = self._symbol()
        icon, cls, kind = aprs.classify(sym if len(sym) == 2 else "/-")
        self.preview.setPixmap(icons.badge(icon, cls))
        self.preview.setToolTip(kind)

    def _ok(self) -> None:
        call = self.call.text().strip().upper()
        remote = self.mode.currentData() == "remote"
        if remote and not self.remote_url.text().strip():
            self.tabs.setCurrentIndex(4)
            QMessageBox.warning(self, "Settings", "Enter the address of the server to connect to.")
            return
        if not call and not remote:
            self.tabs.setCurrentIndex(0)
            QMessageBox.warning(self, "Settings", "Enter your callsign.")
            return
        if not remote and len(self._symbol()) != 2:
            self.tabs.setCurrentIndex(0)
            QMessageBox.warning(self, "Settings", "A custom symbol is two characters: the table (/ or \\) then the code, like /-.")
            return
        if not remote and self.ig_on.isChecked() and not self.ig_pass.text().strip():
            self.tabs.setCurrentIndex(5)
            QMessageBox.warning(self, "Settings", "Enter your APRS-IS passcode, or press Calculate to work it out from your callsign.")
            return
        c = self.cfg
        c.mode, c.remote_url, c.remote_token = self.mode.currentData(), self.remote_url.text().strip(), self.remote_token.text().strip()
        c.share_enabled, c.share_port, c.share_token = self.share.isChecked(), self.share_port.value(), self.share_token.text().strip()
        c.map_cache, c.map_cache_mb = self.cache_on.isChecked(), self.cache_mb.value()
        c.share_require_token, c.share_allow_tx = self.share_require.isChecked(), self.share_tx.isChecked()
        c.callsign, c.lat, c.lon = call, self.lat.value(), self.lon.value()
        c.symbol = self._symbol()
        c.comment, c.path = self.comment.text(), self.path.text().strip()
        c.input_device, c.output_device = self.inp.currentData(), self.out.currentData()
        c.auto_gain = self.auto_gain.isChecked()
        c.alert_device, c.alert_message = self.alert_out.currentData(), self.a_msg.isChecked()
        c.alert_new_station, c.alert_sent = self.a_new.isChecked(), self.a_sent.isChecked()
        c.tx_lead_ms, c.beacon_minutes, c.nomad_url = self.lead.value(), self.beacon.value(), self.nomad.text().strip()
        c.igate_enabled, c.igate_server, c.igate_passcode = self.ig_on.isChecked(), self.ig_server.text().strip(), self.ig_pass.text().strip()
        c.igate_to_rf, c.igate_radius_km = self.ig_to_rf.isChecked(), self.ig_radius.value()
        c.digi_enabled, c.digi_wide = self.dg_on.isChecked(), bool(self.dg_mode.currentData())
        c.save()
        self.accept()


class MainWindow(QMainWindow):
    remote_tx_requested = Signal(dict)  # emitted from share-server threads, handled on the GUI thread
    igate_status = Signal(str)  # from the iGate's thread
    igate_to_rf = Signal(bytes)  # a frame the iGate wants transmitted

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("simpleaprs")
        self.resize(1200, 750)
        self.cfg = Config.load()
        self.radio = Radio()
        self.alerts = Alerts()
        self.alerts.muted = self.cfg.alerts_muted
        self.radio.frame_received.connect(self.on_frame)
        self.radio.gain_message.connect(self._note)
        self.radio.level.connect(self._on_level)
        self._level_sent = 0.0
        self._tx_what = "packet"  # what the transmission in progress is, for the status row
        self.meter_idle = QTimer(self, singleShot=True, interval=1500)  # a followed server that goes quiet
        self.meter_idle.timeout.connect(lambda: self.meter.setValue(0))
        self.radio.tx_started.connect(lambda: self._note("TRANSMITTING"))
        self.radio.tx_finished.connect(self._tx_done)
        self.stations: dict[str, aprs.Packet] = {}
        self.heard: dict[str, float] = {}  # station -> epoch seconds last heard
        self.live: set[str] = set()  # stations heard this session; the rest are remembered ones, drawn grey
        self.wx: dict[str, dict] = {}  # station -> latest weather fields
        self.roles: dict[str, set[str]] = {}  # station -> {"digi", "igate"}, from what we have seen it do
        self.raw: dict[str, bytes] = {}  # station -> raw frame of its last position packet
        self.trails: dict[str, trails.Trail] = {}  # station -> where it has been, oldest first
        self.msg_log: list[tuple[str, str]] = []  # (kind "direct" | "bulletin", line) for the Messages tab, so its filter can redraw
        self.bulletins: dict[tuple[str, str], str] = {}  # (sender, BLN address) -> last text logged: senders repeat them
        self.save_timer = QTimer(self, singleShot=True, interval=5000)
        self.save_timer.timeout.connect(self._save_stations)
        self.server: MapServer | None = None
        self.tile_cache = tile_cache(self.cfg.map_cache_mb)
        self.hub = Hub()  # traffic replay/fan-out for network clients
        self.share: ShareServer | None = None
        self.share_note = ""
        self.remote = RemoteClient()
        self.remote_ident: Config | None = None  # who the server we follow says it is
        self._synced = False  # remote feed: backlog replay finished
        self._map_loaded = False
        self._mode = self.cfg.mode
        self.remote.event.connect(self._on_remote_event)
        self.remote.state.connect(self._remote_state)
        self.remote.tx_result.connect(lambda ok, msg: self._note("Sent through the server's radio" if ok else f"Server refused: {msg}"))
        self.remote_tx_requested.connect(self._remote_tx)
        self.igate: IGate | None = None
        self.digi: Digipeater | None = None
        self.igate_status.connect(self._igate_status)
        self.igate_to_rf.connect(self._igate_transmit)

        self.map = QWebEngineView()
        map_page = MapPage(self.map)
        map_page.station_action.connect(self._station_action)
        self.map.setPage(map_page)
        self.packets = QTextEdit(readOnly=True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Station", "Heard", "Position", "Comment"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._center_row)
        self.msgs = QTextEdit(readOnly=True)
        self.to = QLineEdit(); self.to.setPlaceholderText("To callsign")
        self.text = QLineEdit(); self.text.setPlaceholderText("Message (max 67 chars)"); self.text.setMaxLength(67)
        self.text.returnPressed.connect(self.send_message)
        send = QPushButton("Send"); send.clicked.connect(self.send_message)
        row = QHBoxLayout(); row.addWidget(self.to, 1); row.addWidget(self.text, 4); row.addWidget(send)
        self.bulletin_no = QComboBox(); [self.bulletin_no.addItem(c + (" (urgent)" if c == "BLNX" else ""), c) for c in [f"BLN{i}" for i in range(10)] + ["BLNX"]]; self.bulletin_no.setCurrentIndex(1)
        self.bulletin_text = QLineEdit(); self.bulletin_text.setPlaceholderText("Bulletin (max 67 chars)"); self.bulletin_text.setMaxLength(67)
        self.bulletin_text.returnPressed.connect(self.send_bulletin)
        send_bulletin = QPushButton("Send bulletin"); send_bulletin.clicked.connect(self.send_bulletin)
        brow = QHBoxLayout(); brow.addWidget(self.bulletin_no); brow.addWidget(self.bulletin_text, 1); brow.addWidget(send_bulletin)
        self.msg_filter = QComboBox()
        for label, key in MESSAGE_FILTERS:
            self.msg_filter.addItem(label, key)
        self.msg_filter.setCurrentIndex(max(0, self.msg_filter.findData(self.cfg.msg_filter)))
        self.msg_filter.currentIndexChanged.connect(self._msg_filter_changed)
        fr = QHBoxLayout(); fr.addWidget(QLabel("Show")); fr.addWidget(self.msg_filter, 1)
        mt = QWidget(); lay = QVBoxLayout(mt); lay.addLayout(fr); lay.addWidget(self.msgs); lay.addLayout(row); lay.addLayout(brow)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.table, "Stations"); self.tabs.addTab(mt, "Messages"); self.tabs.addTab(self.packets, "Raw packets")
        split = QSplitter(); split.addWidget(self.map); split.addWidget(self.tabs); split.setStretchFactor(0, 3); split.setStretchFactor(1, 2)
        split.setCollapsible(0, False); split.setSizes([750, 450])
        self.map.setMinimumWidth(400)
        self.setCentralWidget(split)

        tb = self.addToolBar("main"); tb.setMovable(False)
        tb.addAction("Beacon now", self.beacon_now)
        tb.addAction("Settings", self.open_settings)
        mute = tb.addAction("Mute alerts"); mute.setCheckable(True); mute.setChecked(self.cfg.alerts_muted)
        mute.toggled.connect(self._mute)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); tb.addWidget(spacer)
        tb.addWidget(QLabel(" Show "))
        self.filter = QComboBox()
        for label, key in FILTERS:
            self.filter.addItem(label, key)
        self.filter.setCurrentIndex(max(0, self.filter.findData(self.cfg.map_filter)))
        self.filter.currentIndexChanged.connect(self._filter_changed)
        tb.addWidget(self.filter)

        # Second row: what state we are in, and what just happened.
        self.addToolBarBreak()
        sb = self.addToolBar("status"); sb.setMovable(False)
        self.status = QLabel("Starting..."); sb.addWidget(self.status)
        sb.addSeparator()
        self.activity = QLabel(); sb.addWidget(self.activity)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); sb.addWidget(spacer)
        self._igate_sep = sb.addSeparator()
        self.igate_label = QLabel(); self._igate_action = sb.addWidget(self.igate_label)
        self._igate_sep.setVisible(False); self._igate_action.setVisible(False)
        sb.addSeparator(); sb.addWidget(QLabel(" RX audio "))
        self.meter = QProgressBar(); self.meter.setMaximumWidth(120); self.meter.setTextVisible(False); sb.addWidget(self.meter)

        self.timer = QTimer(self); self.timer.timeout.connect(self.beacon_now)
        self._load_stations()
        QTimer.singleShot(0, self.startup)

    @property
    def remote_mode(self) -> bool:
        return self.cfg.mode == "remote"

    @property
    def ident(self) -> Config:
        """Who this station is: us, or the server we are following."""
        return self.remote_ident if self.remote_mode and self.remote_ident else self.cfg

    @property
    def store(self):
        return STATIONS_REMOTE_PATH if self.remote_mode else STATIONS_PATH

    def _idle_status(self) -> str:
        return "Listening" + self.share_note

    def _note(self, text: str) -> None:
        """What just happened, with the time, on the status row."""
        self.activity.setText(f"{time.strftime('%H:%M:%S')}  {text}")

    def _tx_done(self) -> None:
        self._note(f"Sent {self._tx_what}")
        if self.cfg.alert_sent:
            self.alerts.play("sent")

    def _mute(self, on: bool) -> None:
        self.alerts.muted = self.cfg.alerts_muted = on
        self.cfg.save()

    def startup(self) -> None:
        if not self.cfg.configured and not self.remote_mode:
            QMessageBox.information(self, "Welcome", "Let's set up your callsign, location and audio devices.")
            if not self.open_settings():
                return
        self.apply()

    def open_settings(self) -> bool:
        ok = SettingsDialog(self.cfg, self).exec() == QDialog.DialogCode.Accepted
        if ok:
            self.apply()
        return ok

    def apply(self) -> None:
        if self.server:
            self.server.stop()
        if self._mode != self.cfg.mode:  # switched between local radio and a remote server: different station memory
            self._save_stations(self._mode == "remote")
            self._mode = self.cfg.mode
            self.stations.clear(); self.heard.clear(); self.live.clear(); self.wx.clear(); self.roles.clear(); self.raw.clear(); self.trails.clear()
            self.table.setRowCount(0)
            self._load_stations()
        self.tile_cache.max_bytes = self.cfg.map_cache_mb * 1024 * 1024
        self.tile_cache.trim()
        self.server = MapServer(self.cfg.nomad_url, self.tile_cache if self.cfg.map_cache else None)
        self._map_loaded = False
        self.map.load(QUrl(self.server.base + "/"))
        self.map.loadFinished.connect(self._map_ready)
        self.alerts.set_device(self.cfg.alert_device)
        self.timer.stop()
        self._stop_share()
        self._stop_igate()
        self.digi = None
        self.share_note = ""
        if self.remote_mode:
            self.radio.stop()
            self.meter.setValue(0)
            self.remote_ident, self._synced = None, False
            self.remote.start(self.cfg.remote_url, self.cfg.remote_token)
            self.status.setText(f"Connecting to {self.cfg.remote_url}...")
            return
        self.remote.stop()
        self._start_share()
        listening = self.radio.start(self.cfg.input_device, self.cfg.output_device, self.cfg.auto_gain)
        self._start_igate()
        if self.cfg.digi_enabled and self.cfg.configured:
            self.digi = Digipeater(self.cfg.callsign, self.cfg.digi_wide)
        self.status.setText(self._idle_status() if listening else "No audio input - check Settings" + self.share_note)
        if self.cfg.beacon_minutes:
            self.timer.start(self.cfg.beacon_minutes * 60_000)

    def _start_igate(self) -> None:
        if not (self.cfg.igate_enabled and self.cfg.configured):
            return
        self.igate = IGate(self.cfg, self.igate_status.emit, self.igate_to_rf.emit)
        self.igate.start()
        self._set_igate_label("iGate: starting...")

    def _stop_igate(self) -> None:
        if self.igate:
            self.igate.stop()
            self.igate = None
        self._set_igate_label("")

    def _set_igate_label(self, text: str) -> None:
        self.igate_label.setText(" " + text + " " if text else "")
        self._igate_sep.setVisible(bool(text)); self._igate_action.setVisible(bool(text))

    def _igate_status(self, text: str) -> None:
        if self.igate:  # a late message from a gateway that has since been stopped is not news
            self._set_igate_label(text)

    def _igate_transmit(self, raw: bytes) -> None:
        """The iGate wants a message from the internet put on the air."""
        if self.remote_mode or not self.igate:
            return
        try:
            self._send_frame(Frame.decode(raw), relay=True)
        except ValueError:
            pass

    def _relay(self, frame: Frame) -> None:
        """A frame we just heard: offer it to the iGate, and repeat it if we are a digipeater and it asks for that."""
        if self.igate:
            self.igate.send_rf(frame)
        out = self.digi.digipeat(frame) if self.digi else None
        if out:  # a little random wait, so two digipeaters that heard the same packet don't key up together
            QTimer.singleShot(random.randint(300, 1500), lambda: self._send_frame(out, relay=True))

    def _stop_share(self) -> None:
        if self.share:
            self.share.stop()
            self.share = None

    def _start_share(self) -> None:
        if not self.cfg.share_enabled:
            return
        try:
            self.share = ShareServer(self.hub, self.cfg.share_port, self.cfg.share_token, self.cfg.share_require_token,
                                     self.cfg.share_allow_tx, self._hello, self.remote_tx_requested.emit,
                                     snapshot=self._stations_data)
            self.share_note = f" - sharing at http://{lan_ip()}:{self.share.port}"
        except OSError as e:
            self.share_note = f" - sharing failed: {e}"

    def _hello(self) -> dict:
        c = self.cfg
        return {"callsign": c.callsign, "lat": c.lat, "lon": c.lon, "symbol": c.symbol, "comment": c.comment, "path": c.path}

    def _remote_tx(self, req: dict) -> None:
        """A client asked this radio to transmit (already authenticated and validated)."""
        if self.remote_mode or not self.cfg.share_allow_tx:
            return
        if req["type"] == "beacon":
            if not (self.cfg.lat or self.cfg.lon):  # beacon_now would pop a dialog nobody is there to answer
                return
            self._log_message("direct", f"{time.strftime('%H:%M:%S')}  (remote client sent a beacon)")
            self.beacon_now()
        else:
            msg_id = "" if req["type"] == "bulletin" else str(int(time.time()) % 100000)
            self.transmit(aprs.build_message(req["to"], req["text"], msg_id))
            self._log_message("bulletin" if req["type"] == "bulletin" else "direct", f"{time.strftime('%H:%M:%S')}  remote client -> {req['to']}: {req['text']}")

    def _on_remote_event(self, ev: dict) -> None:
        if "hello" in ev:
            h = ev["hello"]
            self.remote_ident = Config(**{k: h[k] for k in ("callsign", "lat", "lon", "symbol", "comment", "path") if k in h})
            self.status.setText(f"Connected to {self.cfg.remote_url}, following {self.remote_ident.callsign}"
                                + ("" if h.get("allow_tx") else " (view only)"))
            if self._map_loaded:
                self._plot_me()
                for p in self.stations.values():
                    self._plot(p)
        elif "level" in ev:
            self.meter.setValue(int(float(ev["level"]) * 100))
            self.meter_idle.start()
        elif "snapshot" in ev:
            self._merge_saved(ev["snapshot"])
        elif ev.get("sync"):
            self._synced = True
        elif "raw" in ev:
            raw = bytes.fromhex(ev["raw"])
            if ev.get("tx"):
                self._on_remote_tx_frame(raw, ev.get("t", time.time()), ev.get("relay", False))
            else:
                self.on_frame(raw, ev.get("t"), replay=not self._synced)

    def _on_remote_tx_frame(self, raw: bytes, t: float, relay: bool = False) -> None:
        try:
            frame = Frame.decode(raw)
        except ValueError:
            return
        stamp = time.strftime("%H:%M:%S", time.localtime(t))
        self.packets.append(f"{stamp}  TX {frame}")
        p = aprs.parse(frame)
        if p.kind == "message" and not p.text.startswith("ack") and not relay:  # a relayed one is someone else's message
            self._log_message("bulletin" if aprs.is_bulletin(p.to) else "direct", f"{stamp}  me -> {p.to}: {p.text}")

    def _on_level(self, v: float) -> None:
        self.meter.setValue(int(v * 100))
        now = time.monotonic()
        if now - self._level_sent >= 0.1 and self.hub.count():  # clients meter it too, ten times a second at most
            self._level_sent = now
            self.hub.publish({"level": round(v, 2)}, False)

    def _remote_state(self, text: str) -> None:
        self.status.setText(text)
        self.meter.setValue(0)

    def _map_ready(self) -> None:
        self.map.loadFinished.disconnect(self._map_ready)
        self._map_loaded = True
        self._plot_me(center=True)
        for p in self.stations.values():
            self._plot(p)

    def _plot_me(self, center: bool = False) -> None:
        me = self.ident
        if (me.lat or me.lon) and self._map_loaded:
            icon, _, kind = aprs.classify(me.symbol)
            info = [["Position", f"{me.lat:.5f}, {me.lon:.5f}"], ["Symbol", f"{me.symbol} ({kind})"],
                    ["Comment", me.comment], ["Path", me.path], ["", "This is you" if not self.remote_mode else "The radio you are following"]]
            call = json.dumps(me.callsign)
            self._js(f"setStation({call},{me.lat},{me.lon},{call},true,{json.dumps(info)},false,{json.dumps(icon)},\"me\");"
                     + (f"centerOn({me.lat},{me.lon},9)" if center else ""))

    def _js(self, code: str) -> None:
        self.map.page().runJavaScript(code)

    def _visible(self, call: str) -> bool:
        """Does this station pass the toolbar filter?"""
        if self.cfg.map_filter == "all":
            return True
        p, weather = self.stations.get(call), bool(self.wx.get(call))
        if p:
            k = aprs.kinds(p.symbol, weather, self.roles.get(call, set()))
        else:  # heard only as positionless weather (or not a station at all)
            k = {"weather"} if weather else set()
        return self.cfg.map_filter in k

    def _refresh_row(self, call: str) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == call:
                self.table.setRowHidden(r, not self._visible(call))

    def _filter_changed(self) -> None:
        self.cfg.map_filter = self.filter.currentData()
        self.cfg.save()
        for p in self.stations.values():
            self._plot(p)
        for r in range(self.table.rowCount()):
            self.table.setRowHidden(r, not self._visible(self.table.item(r, 0).text()))

    def _plot(self, p: aprs.Packet) -> None:
        if p.lat is not None and self._map_loaded:  # _map_ready plots everything once the page is up
            via = ",".join(str(a) for a in p.frame.path)
            stale = p.source not in self.live
            icon, cls, kind = aprs.classify(p.symbol, bool(self.wx.get(p.source)), self.roles.get(p.source, set()))
            info = [["Heard", self._when(self.heard.get(p.source)) + (" (previous session)" if stale else "")],
                    ["Position", f"{p.lat:.5f}, {p.lon:.5f}"],
                    ["Type", kind], ["Symbol", p.symbol], ["Comment", p.comment], ["Via", via],
                    ["Sent by", str(p.frame.src) if p.obj else ""],
                    *[[k, v] for k, v in self.wx.get(p.source, {}).items()], ["Raw", str(p.frame)]]
            segs: list[dict] = []
            if not p.obj:  # an object's path is its sender's, not its own
                segs, hops = aprs.route(p.frame, self._pos, self._me())
                info.insert(info.index(["Raw", str(p.frame)]), ["Route", hops])
            temp = self.wx.get(p.source, {}).get("Temp", "")
            label = json.dumps(f"{p.source} {temp}" if self.cfg.map_filter == "weather" and temp else p.source)
            name = json.dumps(p.source)
            self._js(f"setStation({name},{p.lat},{p.lon},{label},false,{json.dumps(info)},{json.dumps(stale)},"
                     f"{json.dumps(icon)},{json.dumps(cls)},{json.dumps(segs)},{json.dumps(self._visible(p.source))},"
                     f"{json.dumps(trails.line(self.trails.get(p.source, [])))})")

    def _pos(self, call: str) -> tuple[float, float] | None:
        p = self.stations.get(call)
        return (p.lat, p.lon) if p else None

    def _me(self) -> tuple[float, float] | None:
        me = self.ident
        return (me.lat, me.lon) if me.lat or me.lon else None

    @staticmethod
    def _when(t: float | None) -> str:
        if t is None:
            return ""
        return time.strftime("%H:%M:%S" if time.strftime("%F", time.localtime(t)) == time.strftime("%F") else "%Y-%m-%d %H:%M", time.localtime(t))

    def _load_stations(self) -> None:
        try:
            saved = json.loads(self.store.read_text())
        except (OSError, ValueError):
            return
        if isinstance(saved, list):  # first version of the file: just the stations
            saved = {"stations": saved}
        self._merge_saved(saved)

    def _merge_saved(self, saved: dict) -> None:
        """Add remembered stations (from disk, or a server's snapshot). Anything we already know
        about that is newer is kept, and none of it counts as heard this session."""
        for c, r in saved.get("roles", {}).items():
            self.roles.setdefault(c, set()).update(r)
        for rec in saved.get("stations", []):
            try:
                raw = bytes.fromhex(rec["raw"])
                p = aprs.parse(Frame.decode(raw))
                heard = float(rec["heard"])
                if p.lat is None or heard < self.heard.get(p.source, 0):
                    continue
                self.stations[p.source], self.raw[p.source] = p, raw
                self.heard[p.source] = heard
                self.wx[p.source] = rec.get("wx") or {}
                theirs, ours = trails.clean(saved.get("trails", {}).get(p.source)), self.trails.get(p.source, [])
                self.trails[p.source] = theirs if len(theirs) > len(ours) else (ours or trails.Trail([[p.lat, p.lon, int(heard)]]))
                self._update_table(p, self._when(heard))
                if self._map_loaded:
                    self._plot(p)
            except (KeyError, ValueError, TypeError, AttributeError):
                continue

    def _stations_data(self) -> dict:
        """What we remember about stations, in the form stored on disk and sent to clients. Also called
        from a server thread, so it works from copies of the dicts."""
        recent = sorted(list(self.stations), key=lambda c: self.heard.get(c, 0), reverse=True)[:500]
        return {"stations": [{"raw": self.raw[c].hex(), "heard": self.heard.get(c, 0), "wx": dict(self.wx.get(c, {}))}
                             for c in recent if c in self.raw],
                "roles": {c: sorted(r) for c, r in list(self.roles.items())},
                "trails": {c: list(self.trails[c]) for c in recent if len(self.trails.get(c, ())) >= 2}}

    def _save_stations(self, remote: bool | None = None) -> None:
        path = (STATIONS_REMOTE_PATH if remote else STATIONS_PATH) if remote is not None else self.store
        data = self._stations_data()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        except OSError:
            pass

    def _center_row(self, row: int, _col: int) -> None:
        p = self.stations.get(self.table.item(row, 0).text())
        if p and p.lat is not None:
            self._js(f"centerOn({p.lat},{p.lon},12)")

    def on_frame(self, raw: bytes, t: float | None = None, replay: bool = False) -> None:
        """Handle one received frame. t/replay are for frames from a remote server, which carry their
        own time, and whose backlog must not ring alerts or send acks."""
        try:
            frame = Frame.decode(raw)
        except ValueError:
            return
        p = aprs.parse(frame)
        now = t if t is not None else time.time()
        stamp = time.strftime("%H:%M:%S", time.localtime(now))
        if not self.remote_mode:
            self.hub.publish({"t": now, "raw": raw.hex()})
            if not replay:
                self._relay(frame)
        for digi in aprs.digipeaters(frame):
            self._add_role(digi, "digi")
        if p.gated:
            self._add_role(p.source, "igate")
        if self._own_echo(frame):
            digi = next(str(a).rstrip("*") for a in reversed(frame.path) if a.repeated)
            self.packets.append(f"{stamp}  {frame}   <-- OUR PACKET, repeated by {digi}")
            if not replay:
                self._note(f"Our packet was repeated by {digi}")
            return
        self.packets.append(f"{stamp}  {frame}")
        if not replay:
            self._note(f"Heard {p.source} ({p.kind})")
        if p.inner:  # third-party: a message for us can arrive this way, relayed by an iGate
            if p.inner.kind == "message":
                self._on_message(p.inner, stamp, via=p.source, replay=replay)
                return
            p.comment = f"third-party: {p.inner.frame}"
        if p.kind == "message":
            self._on_message(p, stamp, replay=replay)
            return
        if p.weather:
            self.wx[p.source] = p.weather
        if p.kind == "position":
            if p.source not in self.stations and self.cfg.alert_new_station and not replay:
                self.alerts.play("station")
            if p.lat is not None:
                trails.extend(self.trails.setdefault(p.source, trails.Trail()), p.lat, p.lon, now)
            self.stations[p.source], self.raw[p.source] = p, raw
        known = p.source in self.stations
        if p.kind == "position" or (p.kind == "weather" and known):
            self.heard[p.source] = now
            self.live.add(p.source)
            self._plot(self.stations[p.source])  # a weather report updates the popup of the station's last position
            if p.kind == "position":  # stations whose route passes through this one can now draw that hop
                for q in list(self.stations.values()):
                    if q is not p and any(str(a).rstrip("*") == p.source for a in q.frame.path):
                        self._plot(q)
            self.save_timer.start()
        if p.kind == "weather":
            if known:
                p.lat, p.lon = self.stations[p.source].lat, self.stations[p.source].lon
            p.comment = ", ".join(f"{k} {v}" for k, v in p.weather.items())
        self._update_table(p, stamp)

    def _log_message(self, kind: str, line: str) -> None:
        self.msg_log.append((kind, line))
        del self.msg_log[:-2000]
        if self.msg_filter.currentData() in ("all", kind):
            self.msgs.append(line)

    def _msg_filter_changed(self) -> None:
        self.cfg.msg_filter = want = self.msg_filter.currentData()
        self.cfg.save()
        self.msgs.clear()
        for kind, line in self.msg_log:
            if want in ("all", kind):
                self.msgs.append(line)

    def _on_message(self, p: aprs.Packet, stamp: str, via: str = "", replay: bool = False) -> None:
        if aprs.is_bulletin(p.to):
            if self.bulletins.get((p.source, p.to)) != p.text:  # the same one again is not news
                self.bulletins[p.source, p.to] = p.text
                self._log_message("bulletin", f"{stamp}  {p.to} from {p.source}: {p.text}" + (f"   (via iGate {via})" if via else ""))
            return
        if p.to != self.ident.callsign:
            return
        self._log_message("direct", f"{stamp}  {p.source}: {p.text}" + (f"   (via iGate {via})" if via else ""))
        if self.cfg.alert_message and not replay:
            self.alerts.play("message")
        if p.msg_id and not replay and not self.remote_mode:  # ack (when following a server, the server acks)
            self.transmit(aprs.build_message(p.source, f"ack{p.msg_id}"))

    def _add_role(self, call: str, role: str) -> None:
        """Remember what a station has been seen doing, and redraw it if that changes its icon."""
        have = self.roles.setdefault(call, set())
        if role in have:
            return
        have.add(role)
        self.save_timer.start()
        if call in self.stations:
            self._plot(self.stations[call])
        self._refresh_row(call)

    def _own_echo(self, frame: Frame) -> bool:
        """True when a digipeater has repeated one of our own packets back to us."""
        return str(frame.src).rstrip("*") == self.ident.callsign and any(a.repeated for a in frame.path)

    def _update_table(self, p: aprs.Packet, stamp: str) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == p.source:
                break
        else:
            r = self.table.rowCount(); self.table.insertRow(r)
        pos = f"{p.lat:.4f}, {p.lon:.4f}" if p.lat is not None else ""
        for c, v in enumerate([p.source, stamp, pos, p.comment or p.text]):
            self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.setRowHidden(r, not self._visible(p.source))

    def transmit(self, info: bytes) -> None:
        if not self.cfg.configured:
            return
        self._send_frame(aprs.make_frame(self.cfg.callsign, info, self.cfg.path))

    def _send_frame(self, frame: Frame, relay: bool = False) -> None:
        """Put a frame on the air. relay: it is someone else's packet that we are passing on (digipeating or
        gating), which clients following this radio must not show as our own message."""
        self.packets.append(f"{time.strftime('%H:%M:%S')}  TX {frame}" + ("   (relayed)" if relay else ""))
        self._tx_what = "relayed packet" if relay else {"position": "beacon", "message": "message"}.get(aprs.parse(frame).kind, "packet")
        self.hub.publish({"t": time.time(), "raw": frame.encode().hex(), "tx": True, **({"relay": True} if relay else {})})
        self.radio.transmit(frame.encode(), self.cfg.tx_lead_ms, self.cfg.tx_level)

    def beacon_now(self) -> None:
        if self.remote_mode:
            self.remote.send({"type": "beacon"})
            return
        if not (self.cfg.lat or self.cfg.lon):
            QMessageBox.information(self, "Beacon", "Set your latitude/longitude in Settings first.")
            return
        self.transmit(aprs.build_position(self.cfg.lat, self.cfg.lon, self.cfg.symbol, self.cfg.comment))

    def send_message(self) -> None:
        to, text = self.to.text().strip().upper(), self.text.text().strip()
        if not to or not text:
            return
        if self.remote_mode:  # the server's TX feed brings back the "me ->" line
            self.remote.send({"type": "message", "to": to, "text": text})
            self.text.clear()
            return
        self.transmit(aprs.build_message(to, text, str(int(time.time()) % 100000)))
        self._log_message("direct", f"{time.strftime('%H:%M:%S')}  me -> {to}: {text}")
        self.text.clear()

    def send_bulletin(self) -> None:
        to, text = self.bulletin_no.currentData(), self.bulletin_text.text().strip()
        if not text:
            return
        if self.remote_mode:
            self.remote.send({"type": "bulletin", "to": to, "text": text})
        else:
            self.transmit(aprs.build_message(to, text))  # no message number: nobody acks a bulletin
            self._log_message("bulletin", f"{time.strftime('%H:%M:%S')}  me -> {to}: {text}")
        self.bulletin_text.clear()

    def _station_action(self, what: str, call: str) -> None:
        """A button in a station's map popup."""
        if what == "message" and call:
            self.tabs.setCurrentIndex(1)
            self.to.setText(call)
            self.text.setFocus()
        elif what == "clear-inactive":
            self._forget_inactive()

    def _forget_inactive(self) -> None:
        """The legend's button: drop the stations not heard since we started (shown grey) from the map, the
        table and the file. A followed server remembers them too, and sends them again when we reconnect."""
        gone = sorted(c for c in self.stations if c not in self.live)
        if not gone:
            return QMessageBox.information(self, "Clear inactive", "Every station on the map was heard since the program started.")
        if QMessageBox.question(self, "Clear inactive", f"Forget the {len(gone)} stations not heard since the program started?"
                                ) != QMessageBox.StandardButton.Yes:
            return
        for c in gone:
            for d in (self.stations, self.raw, self.heard, self.wx, self.roles, self.trails):
                d.pop(c, None)
        names = set(gone)
        for r in reversed(range(self.table.rowCount())):
            if self.table.item(r, 0).text() in names:
                self.table.removeRow(r)
        self._js(f"removeStations({json.dumps(gone)})")
        self._save_stations()
        self._note(f"Forgot {len(gone)} stations")

    def closeEvent(self, e) -> None:
        self._save_stations()
        self.radio.stop()
        self.remote.stop()
        self._stop_share()
        self._stop_igate()
        if self.server:
            self.server.stop()
        super().closeEvent(e)


def main() -> None:
    # WebGL (needed by the map) hangs in QtWebEngine on some Wayland setups; XWayland works.
    if os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
