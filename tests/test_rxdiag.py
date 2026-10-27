import numpy as np
from simpleaprs.afsk import Demodulator, modulate
from simpleaprs.ax25 import Address, Frame, HdlcDecoder, hdlc_bits, plausible_header
from simpleaprs.rxdiag import explain


def make():
    return Frame(Address.parse("N0CALL-7"), Address.parse("APRS"), [Address.parse("WIDE1-1")], b"!4903.50N/07201.75W-Test")


def hear(demod, audio):
    for i in range(0, len(audio), 1024):
        demod.feed(audio[i:i + 1024])


def test_good_packet_counts_ok():
    d = Demodulator()
    hear(d, modulate(make().encode()))
    assert d._hdlc.stats == {"ok": 1, "corrupt": 0, "slip": 0}


def feed_bits(bits):
    h = HdlcDecoder()
    return h, [f for bit in bits if (f := h.feed(bit)) is not None]


def test_damaged_packet_is_reported_with_who():
    bits = hdlc_bits(make().encode())
    bits[30 * 8 + 200] ^= 1  # one flipped bit in the payload: the checksum fails
    h, frames = feed_bits(bits)
    assert frames == [] and h.stats == {"ok": 0, "corrupt": 1, "slip": 0}
    assert h.last_bad == "N0CALL-7>APRS"


def test_lost_bit_is_a_slip():
    bits = hdlc_bits(make().encode())
    del bits[30 * 8 + 200]
    h, frames = feed_bits(bits)
    assert frames == [] and h.stats["slip"] == 1


def test_noise_alone_reports_nothing():
    d = Demodulator()
    hear(d, np.random.default_rng(5).normal(0, 0.3, 48000 * 60).astype(np.float32))
    assert d._hdlc.stats == {"ok": 0, "corrupt": 0, "slip": 0}


def test_plausible_header():
    assert plausible_header(make().encode()) == "N0CALL-7>APRS"
    assert plausible_header(bytes(range(40))) == ""
    assert plausible_header(b"short") == ""


def test_explain():
    assert explain(3, 0, 0, 0, 600) == ""
    assert "could not decode (looked like K5X>APRS)" in explain(0, 2, 0, 0, 600, "K5X>APRS")
    assert "weak or noisy" in explain(0, 2, 0, 0, 600)
    assert "lost or added" in explain(0, 0, 2, 0, 600)
    assert "clipping" in explain(1, 0, 0, 300, 600)
    assert "1 decoded fine" in explain(1, 1, 0, 0, 600)
