import socket
import threading
import time
from types import SimpleNamespace

from simpleaprs import aprs
from simpleaprs.ax25 import Frame
from simpleaprs.digipeater import Digipeater
from simpleaprs.igate import IGate, is_to_rf, parse_server, passcode, rf_to_is

ME = "N0CALL-7"


def frame(src, info, path="WIDE1-1"):
    return aprs.make_frame(src, info, path)


def test_passcode():
    assert passcode("N0CALL") == 13023
    assert passcode("n0call-7") == 13023  # SSID and case don't matter


def test_parse_server():
    assert parse_server("rotate.aprs2.net:14580") == ("rotate.aprs2.net", 14580)
    assert parse_server(" noc3.aprs2.net ") == ("noc3.aprs2.net", 14580)


# --- RF -> internet ---------------------------------------------------------------------------------

def test_rf_to_is_line():
    f = frame("W1AW-9", b"!4903.50N/07201.75W-hi", "KD5DIG*,WIDE2-1")
    assert rf_to_is(f, ME) == "W1AW-9>APSMPL,KD5DIG*,WIDE2-1,qAR,N0CALL-7:!4903.50N/07201.75W-hi"
    assert rf_to_is(frame("W1AW-9", b"!x", ""), ME) == "W1AW-9>APSMPL,qAR,N0CALL-7:!x"


def test_rf_to_is_refuses_what_must_not_be_gated():
    assert rf_to_is(frame(ME, b"!x"), ME) is None  # our own
    assert rf_to_is(frame("W1AW", b"!x", "WIDE1-1,NOGATE"), ME) is None
    assert rf_to_is(frame("W1AW", b"!x", "RFONLY"), ME) is None
    assert rf_to_is(frame("W1AW", b"!x", "TCPIP*"), ME) is None  # came from the internet
    assert rf_to_is(frame("W1AW", b"!x", "WIDE1-1,qAR,W5GATE"), ME) is None  # already carries a q construct
    assert rf_to_is(frame("W1AW", b"}W5X>APRS,TCPIP:!x"), ME) is None  # third-party
    assert rf_to_is(frame("W1AW", b"?APRS?"), ME) is None  # query
    assert rf_to_is(frame("W1AW", b""), ME) is None
    assert rf_to_is(frame("N0CALL", b"!x"), ME) is None  # the placeholder callsign
    assert rf_to_is(frame("W1AW", b"line\nbreak"), ME) is None


# --- internet -> RF ---------------------------------------------------------------------------------

def test_is_to_rf_wraps_messages_for_heard_stations():
    heard = lambda c: c == "W5NEAR-9"
    f = is_to_rf("W1AW>APRS,TCPIP*,qAC,T2X::W5NEAR-9 :hello there{12", ME, heard)
    assert f and str(f.src) == ME and not f.path
    assert f.info == b"}W1AW>APRS,TCPIP,N0CALL-7*::W5NEAR-9 :hello there{12"
    p = aprs.parse(f)
    assert p.gated and p.inner.kind == "message" and p.inner.to == "W5NEAR-9" and p.inner.text == "hello there"


def test_is_to_rf_only_messages_to_heard_stations():
    heard = lambda c: c == "W5NEAR-9"
    assert is_to_rf("W1AW>APRS,qAC,T2X::W5FAR     :hi", ME, heard) is None  # not heard here
    assert is_to_rf("W1AW>APRS,qAC,T2X:!4903.50N/07201.75W-", ME, heard) is None  # not a message
    assert is_to_rf("W1AW>APRS,qAC,T2X::BLN1     :net at 8", ME, lambda c: True) is None  # bulletins stay on the internet
    assert is_to_rf("W1AW>APRS,qAC,T2X::N0CALL-7 :for us", ME, lambda c: True) is None
    assert is_to_rf("W1AW>APRS,NOGATE,qAC,T2X::W5NEAR-9 :hi", ME, heard) is None
    assert is_to_rf("# aprsc 2.1 keepalive", ME, heard) is None
    assert is_to_rf("garbage", ME, heard) is None


# --- the connection, against a fake APRS-IS server --------------------------------------------------

class FakeIS:
    def __init__(self, verified=True, push=()):
        self.verified, self.push = verified, list(push)
        self.got: list[str] = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        conn.sendall(b"# aprsc 2.1.15 fake\r\n")
        buf = b""
        conn.settimeout(0.2)
        pushed = False
        while True:
            try:
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
            except socket.timeout:
                pass
            except OSError:
                return
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                self.got.append(line.decode().rstrip("\r"))
                if line.startswith(b"user "):
                    conn.sendall(f"# logresp N0CALL-7 {'verified' if self.verified else 'unverified'}, server FAKE\r\n".encode())
            if self.got and self.verified and not pushed:
                pushed = True
                for line in self.push:
                    conn.sendall(line.encode() + b"\r\n")

    def close(self):
        self.srv.close()


def wait_for(cond, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def gate(server, **kw):
    cfg = SimpleNamespace(callsign=ME, igate_server=f"127.0.0.1:{server.port}", igate_passcode="13023", igate_to_rf=False,
                          igate_radius_km=50, lat=30.0, lon=-97.0, symbol="/&", comment="test igate")
    for k, v in kw.items():
        setattr(cfg, k, v)
    status, to_rf = [], []
    g = IGate(cfg, status.append, to_rf.append)
    g.start()
    return g, status, to_rf


def test_gates_rf_packets_to_the_server():
    srv = FakeIS()
    g, status, _ = gate(srv)
    try:
        assert wait_for(lambda: g._online)
        g.send_rf(frame("W1AW-9", b"!4903.50N/07201.75W-hi", "WIDE1-1"))
        g.send_rf(frame("W1AW-9", b"!4903.50N/07201.75W-hi", "WIDE1-1"))  # the same packet again: not gated twice
        g.send_rf(frame("W5X", b"!x", "TCPIP*"))  # not gated at all
        assert wait_for(lambda: any(l.startswith("W1AW-9>") for l in srv.got))
        time.sleep(0.5)
        assert srv.got[0] == "user N0CALL-7 pass 13023 vers simpleaprs 0.1"  # no filter: nothing is coming back
        assert [l for l in srv.got if l.startswith("W1AW-9>")] == ["W1AW-9>APSMPL,WIDE1-1,qAR,N0CALL-7:!4903.50N/07201.75W-hi"]
        assert any(l.startswith("N0CALL-7>APSMPL,TCPIP*:!") for l in srv.got)  # our own position, so the server knows where we are
        assert any("connected" in s and "1 gated" in s for s in status)
    finally:
        g.stop(); srv.close()


def test_internet_messages_go_to_rf_only_for_stations_heard_directly():
    push = ["W1AW>APRS,TCPIP*,qAC,T2X::W5NEAR-9 :hello{1}", "W1AW>APRS,TCPIP*,qAC,T2X::W5FAR     :nope{2}"]
    srv = FakeIS(push=push)
    g, _, to_rf = gate(srv, igate_to_rf=True, igate_radius_km=75)
    try:
        g.send_rf(frame("W5NEAR-9", b">hi", ""))  # heard with no hops used: direct
        g.send_rf(frame("W5FAR", b">hi", "KD5DIG*,WIDE1-1"))  # heard through a digipeater: not direct
        assert wait_for(lambda: g._online and to_rf)
        time.sleep(0.5)
        assert srv.got[0] == "user N0CALL-7 pass 13023 vers simpleaprs 0.1 filter m/75"
        assert len(to_rf) == 1 and b"W5NEAR-9" in to_rf[0] and b"W5FAR" not in to_rf[0]
        assert Frame.decode(to_rf[0]).info.startswith(b"}W1AW>APRS,TCPIP,N0CALL-7*:")
    finally:
        g.stop(); srv.close()


def test_rejected_passcode_stops_trying():
    srv = FakeIS(verified=False)
    g, status, _ = gate(srv)
    try:
        assert wait_for(lambda: any("did not accept the passcode" in s for s in status))
        assert not g._online
    finally:
        g.stop(); srv.close()


def test_no_passcode_does_not_connect():
    srv = FakeIS()
    g, status, _ = gate(srv, igate_passcode="")
    try:
        assert wait_for(lambda: any("passcode" in s for s in status))
        time.sleep(0.3)
        assert srv.got == []
    finally:
        g.stop(); srv.close()


# --- digipeater -------------------------------------------------------------------------------------

def path(f):
    return [str(a) for a in f.path]


def test_fill_in_answers_wide1_1_only():
    d = Digipeater(ME, wide=False)
    out = d.digipeat(frame("W1AW", b"!x", "WIDE1-1,WIDE2-1"), now=0)
    assert path(out) == [ME + "*", "WIDE2-1"] and out.info == b"!x" and str(out.src) == "W1AW"
    assert d.digipeat(frame("W2AW", b"!x", "WIDE2-1"), now=0) is None  # a fill-in leaves WIDE2 alone
    assert d.digipeat(frame("W3AW", b"!x", "WIDE2-2"), now=0) is None


def test_wide_answers_wide2_n_and_decrements():
    d = Digipeater(ME, wide=True)
    assert path(d.digipeat(frame("W1AW", b"!x", "WIDE2-2"), now=0)) == [ME + "*", "WIDE2-1"]
    assert path(d.digipeat(frame("W2AW", b"!x", "WIDE2-1"), now=0)) == [ME + "*"]
    assert path(d.digipeat(frame("W3AW", b"!x", "KD5DIG*,WIDE2-1"), now=0)) == ["KD5DIG*", ME + "*"]  # after an earlier digipeater
    assert d.digipeat(frame("W4AW", b"!x", "WIDE3-3"), now=0) is None  # too many hops asked for
    assert d.digipeat(frame("W5AW", b"!x", "WIDE2-3"), now=0) is None
    assert d.digipeat(frame("W6AW", b"!x", ""), now=0) is None  # heard direct, nobody asked


def test_answers_to_its_own_callsign_and_never_repeats_itself():
    d = Digipeater(ME, wide=False)
    assert path(d.digipeat(frame("W1AW", b"!x", "N0CALL-7,WIDE2-1"), now=0)) == [ME + "*", "WIDE2-1"]
    assert d.digipeat(frame("W2AW", b"!x", "K5OTHER-1"), now=0) is None  # someone else's name
    assert d.digipeat(frame(ME, b"!x", "WIDE1-1"), now=0) is None  # our own packet
    assert d.digipeat(frame("W3AW", b"!x", "KD5DIG*"), now=0) is None  # path already used up


def test_digipeater_ignores_duplicates_for_a_while():
    d = Digipeater(ME, wide=True)
    f = frame("W1AW", b"!x", "WIDE1-1")
    assert d.digipeat(f, now=0)
    assert d.digipeat(f, now=10) is None
    assert d.digipeat(f, now=40)  # long enough later: a new transmission


def test_repeated_frame_roundtrips_on_the_air():
    out = Digipeater(ME, wide=True).digipeat(frame("W1AW", b"!x", "WIDE1-1,WIDE2-1"), now=0)
    back = Frame.decode(out.encode())
    assert str(back) == "W1AW>APSMPL,N0CALL-7*,WIDE2-1:!x" and back.path[0].repeated and not back.path[1].repeated
