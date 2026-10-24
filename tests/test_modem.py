import numpy as np
from simpleaprs.afsk import Demodulator, modulate
from simpleaprs.ax25 import Address, Frame


def make(info=b"!4903.50N/07201.75W-Test"):
    return Frame(Address.parse("N0CALL-7"), Address.parse("APRS"), [Address.parse("WIDE1-1")], info)


def test_frame_roundtrip():
    f = make()
    g = Frame.decode(f.encode())
    assert str(g) == "N0CALL-7>APRS,WIDE1-1:!4903.50N/07201.75W-Test"


def test_modem_loopback():
    for fs in (48000, 44100, 22050):
        audio = modulate(make().encode(), fs)
        pad = np.zeros(fs // 10, dtype=np.float32)
        audio = np.concatenate([pad, audio, pad])
        demod = Demodulator(fs)
        got = []
        for i in range(0, len(audio), 1024):
            got += demod.feed(audio[i:i + 1024])
        assert got and Frame.decode(got[0]).info == make().info, fs


def test_modem_with_noise():
    rng = np.random.default_rng(1)
    audio = modulate(make().encode())
    audio = audio + rng.normal(0, 0.15, audio.shape).astype(np.float32)
    got = Demodulator().feed(audio)
    assert got
