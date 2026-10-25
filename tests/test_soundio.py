from simpleaprs import soundio


def test_find_device(monkeypatch):
    monkeypatch.setattr(soundio, "device_names", lambda inputs: ["USB Audio Device: - (hw:1,0)", "bcm2835 Headphones: - (hw:0,0)"])
    assert soundio.find_device("bcm2835 Headphones: - (hw:0,0)", True) == "bcm2835 Headphones: - (hw:0,0)"
    assert soundio.find_device("usb audio device", True) == "USB Audio Device: - (hw:1,0)"  # the hw suffix can change
    assert soundio.find_device("Nope", True) is None


def test_level_is_reported_for_every_chunk_even_while_transmitting():
    import numpy as np
    levels = []
    r = soundio.SoundRadio(lambda raw: None, on_level=levels.append)
    quiet = np.zeros(4800, dtype=np.int16).tobytes()
    loud = (np.ones(4800) * 16384).astype(np.int16).tobytes()  # half scale -> meter full
    r._chunks.put(quiet)
    r.transmitting = True  # half duplex: not decoded, but the meter still moves
    r._chunks.put(loud)
    r._chunks.put(None)
    r._decode()
    assert levels == [0.0, 1.0]
