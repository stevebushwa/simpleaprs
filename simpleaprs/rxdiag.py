"""Why isn't it decoding? Turns a stretch of receive statistics into a sentence."""
from __future__ import annotations


def explain(ok: int, corrupt: int, slip: int, clipped: int, chunks: int, last_bad: str = "") -> str:
    """What was heard in a stretch of `chunks` tenth-second audio blocks, of which `clipped` hit full scale.
    "" when there is nothing worth saying."""
    bad = corrupt + slip
    parts = []
    if bad:
        who = f" (looked like {last_bad})" if last_bad else ""
        parts.append(f"heard {bad} packet{'s' * (bad != 1)} it could not decode{who}: "
                     + ("bits were corrupted, so the signal is weak or noisy" if corrupt >= slip else
                        "bits were lost or added, so the signal is weak or the audio is distorted"))
    if chunks and clipped / chunks > 0.2:
        parts.append("the input audio is clipping, so turn the radio volume or the input level down")
    if not parts:
        return ""
    return ("Receive: " + "; ".join(parts) + (f". {ok} decoded fine." if ok else ".")).replace(" .", ".")
