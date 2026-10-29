import numpy as np
from simpleaprs.afsk import Demodulator, modulate
from simpleaprs.ax25 import Address, Frame, HdlcDecoder, crc16_x25, damaged_line, hdlc_bits, plausible_header, repair_one_bit
from simpleaprs.rxdiag import explain


def make():
    return Frame(Address.parse("N0CALL-7"), Address.parse("APRS"), [Address.parse("WIDE1-1")], b"!4903.50N/07201.75W-Test")


def hear(demod, audio):
    for i in range(0, len(audio), 1024):
        demod.feed(audio[i:i + 1024])


def test_good_packet_counts_ok():
    d = Demodulator()
    hear(d, modulate(make().encode()))
    assert d._hdlc.stats == {"ok": 1, "fixed": 0, "corrupt": 0, "slip": 0}


def feed_bits(bits):
    h = HdlcDecoder()
    return h, [f for bit in bits if (f := h.feed(bit)) is not None]


def test_one_flipped_bit_is_repaired():
    raw = make().encode()
    bits = hdlc_bits(raw)
    bits[30 * 8 + 200] ^= 1  # one flipped bit in the payload
    h, frames = feed_bits(bits)
    assert frames == [raw] and h.stats == {"ok": 1, "fixed": 1, "corrupt": 0, "slip": 0}


def test_two_flipped_bits_are_reported_not_repaired():
    bits = hdlc_bits(make().encode())
    bits[30 * 8 + 200] ^= 1
    bits[30 * 8 + 230] ^= 1
    h, frames = feed_bits(bits)
    assert frames == [] and h.stats == {"ok": 0, "fixed": 0, "corrupt": 1, "slip": 0}
    assert h.last_bad == "N0CALL-7>APRS"


def test_repair_one_bit():
    good = make().encode()
    data = good + crc16_x25(good).to_bytes(2, "little")
    assert repair_one_bit(data) is None  # it is for frames that failed; an intact one has no single flip that passes
    for i in (0, 77, len(data) * 8 - 1):  # payload, header, and the last FCS bit
        bad = bytearray(data); bad[i >> 3] ^= 1 << (i & 7)
        assert repair_one_bit(bytes(bad)) == data
    two = bytearray(data); two[20] ^= 1; two[21] ^= 1
    assert repair_one_bit(bytes(two)) is None


def test_lost_bit_is_a_slip():
    bits = hdlc_bits(make().encode())
    del bits[30 * 8 + 200]
    h, frames = feed_bits(bits)
    assert frames == [] and h.stats["slip"] == 1


def test_a_frame_whose_closing_flag_was_lost_still_ends_at_the_abort():
    raw = make().encode()
    bits = hdlc_bits(raw, tail_flags=0) + [0] + [1] * 7  # a flag whose last 1 came out wrong: seven ones in a row read as an abort
    h, frames = feed_bits(bits)
    assert frames == [raw] and h.stats["ok"] == 1


def test_damaged_packet_is_kept_for_the_raw_list():
    raw = make().encode()
    bits = hdlc_bits(raw)
    bits[30 * 8 + 200] ^= 1
    bits[30 * 8 + 230] ^= 1
    h, frames = feed_bits(bits)
    (data, whole), = h.take_damaged()
    assert frames == [] and whole and data[:-2] != raw  # the text is there, with the two bad bits in it
    assert h.take_damaged() == []
    line = damaged_line(data, whole)
    assert line.startswith("N0CALL-7>APRS,WIDE1-1:!") and "903.50N/07201.75W-" in line and "DAMAGED" in line


def test_damaged_line_drops_the_checksum_and_the_junk_after_the_text():
    raw = make().encode()
    cut = raw + b"\x01\xfe\x00"  # the end of the text came out as junk
    line = damaged_line(cut, False)
    assert ":!4903.50N/07201.75W-Test   <-- DAMAGED" in line
    whole = damaged_line(raw + b"\xaa\xbb", True)  # a clean flag followed it: the last two bytes were the checksum
    assert ":!4903.50N/07201.75W-Test   <-- DAMAGED" in whole


def test_packet_that_ends_in_the_abort_and_is_damaged_is_counted():
    bits = hdlc_bits(make().encode(), tail_flags=0) + [0] + [1] * 7
    bits[30 * 8 + 200] ^= 1
    bits[30 * 8 + 230] ^= 1
    h, frames = feed_bits(bits)
    assert frames == [] and h.stats["corrupt"] == 1 and len(h.take_damaged()) == 1


def test_noise_alone_reports_nothing():
    d = Demodulator()
    hear(d, np.random.default_rng(5).normal(0, 0.3, 48000 * 60).astype(np.float32))
    assert d._hdlc.stats == {"ok": 0, "fixed": 0, "corrupt": 0, "slip": 0}
    assert d.take_damaged() == []


def test_plausible_header():
    assert plausible_header(make().encode()) == "N0CALL-7>APRS"
    assert plausible_header(bytes(range(40))) == ""
    assert plausible_header(b"short") == ""


def test_explain():
    assert explain(3, 0, 0, 0, 600) == ""
    assert "could not decode (looked like K5X>APRS)" in explain(0, 2, 0, 0, 600, "K5X>APRS")
    assert "weak or noisy" in explain(0, 2, 0, 0, 600)
    assert "lost or added" in explain(0, 0, 2, 0, 600)
    assert "clipping" in explain(1, 0, 0, 20, 40)
    assert explain(1, 0, 0, 1, 40) == ""  # one clipped block among many is not a pattern
    assert "1 decoded fine" in explain(1, 1, 0, 0, 600)


def hear_radio(blocks):
    """Run tenth-second blocks of audio through a SoundRadio's level measuring and return it."""
    from simpleaprs.soundio import SoundRadio
    r = SoundRadio(lambda raw: None, on_level=lambda level: None)
    for b in blocks:
        r._measure(np.clip(b, -1, 1).astype(np.float32))
    return r


def test_clipped_packets_are_noticed_over_open_squelch_hiss():
    # hiss at rms 0.07 the whole time, with a clipped one-second packet every 15 seconds
    rng = np.random.default_rng(3)
    hiss = lambda: rng.normal(0, 0.07, 4800)
    packet = lambda: np.sign(np.sin(np.arange(4800) * 0.3)) * 1.5
    blocks = []
    for _ in range(4):
        blocks += [hiss() for _ in range(140)] + [packet() for _ in range(10)]
    r = hear_radio(blocks)
    s = r.rx_stats()
    assert s["clipped"] >= 30 and s["loud"] < 60  # the hiss itself is not counted as signal
    assert s["peak"] == 1.0
    assert "clipping" in explain(0, 0, 0, s["clipped"], s["loud"])


def test_hiss_and_clean_packets_are_not_clipping():
    rng = np.random.default_rng(4)
    blocks = [rng.normal(0, 0.07, 4800) for _ in range(300)] + [0.5 * np.sin(np.arange(4800) * 0.3) for _ in range(10)]
    r = hear_radio(blocks)
    s = r.rx_stats()
    assert s["loud"] >= 8 and s["clipped"] == 0
    assert 0.45 < s["peak"] < 0.55
    assert r.rx_stats()["peak"] == 0.0  # the peak is since the last look


def test_damaged_packet_waits_and_is_dropped_if_a_decoder_delivered_it():
    from simpleaprs.soundio import SoundRadio
    shown = []
    r = SoundRadio(lambda raw: None, on_damaged=lambda data, whole: shown.append(data))
    raw = make().encode()
    r._frame(raw)  # the other decoder got this one right
    r._held = [(100.0, raw + b"\x00\x01", False), (100.0, b"K5X-1" + raw[5:] + b"\x07", False)]
    r._seen = {k: 100.5 for k in r._seen}
    r._release_damaged(101.0)
    assert shown == []  # still inside the wait
    r._release_damaged(102.5)
    assert shown == [b"K5X-1" + raw[5:] + b"\x07"]  # the copy someone decoded properly is not shown a second time
