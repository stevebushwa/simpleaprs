from simpleaprs import soundio


def test_find_device(monkeypatch):
    monkeypatch.setattr(soundio, "device_names", lambda inputs: ["USB Audio Device: - (hw:1,0)", "bcm2835 Headphones: - (hw:0,0)"])
    assert soundio.find_device("bcm2835 Headphones: - (hw:0,0)", True) == "bcm2835 Headphones: - (hw:0,0)"
    assert soundio.find_device("usb audio device", True) == "USB Audio Device: - (hw:1,0)"  # the hw suffix can change
    assert soundio.find_device("Nope", True) is None
