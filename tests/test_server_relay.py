import time

from simpleaprs import aprs
from simpleaprs.ax25 import Frame
from simpleaprs.config import Config
from simpleaprs.digipeater import Digipeater
from simpleaprs.headless import Server

ME = "N0CALL-7"


class FakeRadio:
    def __init__(self):
        self.sent = []

    def transmit(self, raw, lead_ms, level):
        self.sent.append(Frame.decode(raw))

    def stop(self):
        pass


class FakeGate:
    def __init__(self):
        self.heard = []

    def send_rf(self, frame):
        self.heard.append(frame)

    def stop(self):
        pass


def server(**cfg):
    s = Server(Config(callsign=ME, lat=30.0, lon=-97.0, **cfg), quiet=True)
    s.radio = FakeRadio()
    return s


def wait_for(cond, secs=3):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_heard_frames_go_to_the_igate_and_get_digipeated():
    s = server()
    s.igate, s.digi = FakeGate(), Digipeater(ME, wide=True)
    raw = aprs.make_frame("W1AW-9", aprs.build_position(30.2, -97.2, "/>", "hi"), "WIDE1-1,WIDE2-1").encode()
    s.on_frame(raw)
    assert [str(f.src) for f in s.igate.heard] == ["W1AW-9"]
    assert wait_for(lambda: s.radio.sent)  # after its short random wait
    assert str(s.radio.sent[0]).startswith("W1AW-9>APSMPL,N0CALL-7*,WIDE2-1:!")
    tx = [e for e in s.hub._log if e.get("tx")]
    assert len(tx) == 1 and tx[0]["relay"] is True


def test_a_relayed_message_is_not_logged_as_ours():
    s = server()
    events = []
    s.web_hub.publish = lambda ev, keep=True: events.append(ev)
    msg = aprs.make_frame("W1AW-9", aprs.build_message("K5XYZ", "hello", "9"), "N0CALL-7*").encode()
    s.tracker.on_tx(msg, relay=True)
    assert [e["type"] for e in events] == ["packet"] and events[0]["text"].endswith("(relayed)")
    s.tracker.on_tx(msg)
    assert [e["type"] for e in events][1:] == ["packet", "message"]


def test_nothing_is_relayed_when_neither_is_on():
    s = server()
    s.on_frame(aprs.make_frame("W1AW-9", aprs.build_position(30.2, -97.2), "WIDE1-1").encode())
    time.sleep(0.5)
    assert s.radio.sent == []


def test_igate_message_for_rf_is_sent_as_relay_and_stop_silences_it():
    s = server()
    out = aprs.make_frame(ME, b"}W1AW>APRS,TCPIP,N0CALL-7*::W5NEAR-9 :hi", "")
    s.igate_to_rf(out.encode())
    assert len(s.radio.sent) == 1 and s.hub._log[-1]["relay"] is True
    s.stop()
    s.igate_to_rf(out.encode())
    assert len(s.radio.sent) == 1


def test_igate_without_passcode_refuses_to_start(capsys):
    s = server(igate_enabled=True)
    assert s.start() is False
    assert "passcode" in capsys.readouterr().err
