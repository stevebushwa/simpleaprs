import numpy as np

from simpleaprs import autogain
from simpleaprs.autogain import GainController

FS = 48000


def tone(peak: float, seconds: float = 0.1) -> np.ndarray:
    return (peak * np.sin(2 * np.pi * 1200 * np.arange(int(FS * seconds)) / FS)).astype(np.float32)


def run(ctl, peak, seconds, start=100.0):
    """Feed audio of a fixed peak for a while; return the steps asked for."""
    t, steps = start, []
    while t < start + seconds:
        if s := ctl.feed(tone(peak), FS, t):
            steps.append(s)
        t += 0.1
    return steps


def test_clipping_turns_down_and_waits_to_settle():
    ctl = GainController()
    steps = run(ctl, 1.0, 5)
    assert steps and set(steps) == {-1} and len(steps) <= 3  # not once per chunk


def test_weak_signal_turns_up_after_a_window():
    assert run(GainController(), 0.1, 60)[:1] == [1]


def test_good_level_is_left_alone():
    assert run(GainController(), 0.6, 120) == []


def test_silence_is_not_a_weak_signal():
    assert run(GainController(), 0.0, 120) == []  # a closed squelch must not wind the volume up


def test_one_loud_packet_in_a_quiet_window_keeps_it_there():
    ctl = GainController()
    run(ctl, 0.1, 10)
    assert run(ctl, 0.6, 1, start=110.0) == []
    assert run(ctl, 0.1, 15, start=111.0) == []


def test_volume_step_is_bounded(monkeypatch):
    calls = []

    class Fake:
        name = "fake"
        vol = 98

        def get(self, src): return self.vol
        def set(self, src, pct): calls.append(pct); return True

    monkeypatch.setattr(autogain, "_backend", lambda device: (Fake(), "src"))
    msgs = []
    g = autogain.AutoGain(FS, msgs.append)
    g._busy.acquire()
    g._apply(+1)
    Fake.vol = 6
    g._busy.acquire()
    g._apply(-1)
    assert calls == [100, 5] and len(msgs) == 2


def test_matches_device_names_with_hardware_suffixes():
    assert autogain._matches("USB Audio Device: - (hw:1,0)", "alsa_input.usb", "USB Audio Device Mono")
    assert not autogain._matches("USB Audio Device", "Built-in Audio Analog Stereo")
