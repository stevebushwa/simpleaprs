from simpleaprs import direwolf
from simpleaprs.direwolf import FEND, FESC, TFEND, TFESC, KissParser
from simpleaprs.soundio import SoundRadio


def kiss(payload: bytes, port: int = 0) -> bytes:
    body = bytes([port]) + payload.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(bytes([FEND]), bytes([FESC, TFEND]))
    return bytes([FEND]) + body + bytes([FEND])


def test_kiss_parser_returns_data_frames_across_chunks_and_unescapes():
    payload = b"abc" + bytes([FEND, FESC]) + b"xyz"
    stream = kiss(payload) + kiss(b"second")
    p = KissParser()
    got = []
    for i in range(0, len(stream), 3):  # arrives in small pieces
        got += p.feed(stream[i:i + 3])
    assert got == [payload, b"second"]


def test_kiss_parser_ignores_other_ports_and_junk_between_frames():
    p = KissParser()
    assert p.feed(b"junk" + kiss(b"not ours", port=1) + b"more junk" + kiss(b"ours")) == [b"ours"]


def test_direwolf_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("SIMPLEAPRS_DIREWOLF", "0")
    assert direwolf.available() is None


def test_a_packet_both_decoders_find_is_delivered_once():
    got = []
    r = SoundRadio(got.append)
    r._frame(b"same packet")
    r._frame(b"same packet")
    r._frame(b"another")
    assert got == [b"same packet", b"another"] and r.delivered == 2
