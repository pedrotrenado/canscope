"""Generate plausible traffic for every message in a DBC."""

from __future__ import annotations

import logging
import math
import threading
import time
import zlib

import can
import cantools

log = logging.getLogger(__name__)

DEFAULT_PERIOD = 0.1  # seconds, for messages without GenMsgCycleTime


FLOAT_RANGE = (-1000.0, 1000.0)  # for IEEE float signals without a DBC range


def _signal_range(signal) -> tuple[float, float]:
    """Physical range to simulate: the DBC [min|max], limited to what the raw field holds."""
    if signal.is_float:
        lo, hi = FLOAT_RANGE
    else:
        if signal.is_signed:
            raw_lo, raw_hi = -(1 << (signal.length - 1)), (1 << (signal.length - 1)) - 1
        else:
            raw_lo, raw_hi = 0, (1 << signal.length) - 1
        ends = (raw_lo * signal.scale + signal.offset, raw_hi * signal.scale + signal.offset)
        lo, hi = min(ends), max(ends)
    if signal.minimum is not None and signal.maximum is not None and signal.minimum < signal.maximum:
        lo, hi = max(lo, float(signal.minimum)), min(hi, float(signal.maximum))
    return lo, hi


def signal_value(signal, t: float) -> float:
    """Value of `signal` at time `t`: deterministic, smooth and inside its range.

    Each signal gets its own frequency and phase from a hash of its name, so a
    DBC with many signals does not have them all moving in lockstep.
    """
    h = zlib.crc32(signal.name.encode())
    if signal.choices:
        raws = sorted(signal.choices)  # choice keys are raw values
        raw = raws[int(t / 3.0 + h % len(raws)) % len(raws)]
        return raw * signal.scale + signal.offset
    if signal.length == 1:
        return float(int(t / (0.4 + (h % 7) / 10)) % 2)

    lo, hi = _signal_range(signal)
    freq = 0.05 + (h % 100) / 400  # 0.05..0.3 Hz
    phase = (h >> 8) % 628 / 100
    # Two sines for something less regular than a pure tone.
    wave = 0.75 * math.sin(2 * math.pi * freq * t + phase) + 0.25 * math.sin(7.3 * freq * t)
    value = lo + (hi - lo) * (wave + 1) / 2
    if signal.is_float:
        return value
    # Snap inwards to the signal's resolution so the raw value stays in range.
    raw = (value - signal.offset) / signal.scale
    raw_lo, raw_hi = sorted(((lo - signal.offset) / signal.scale, (hi - signal.offset) / signal.scale))
    raw = min(max(round(raw), math.ceil(raw_lo)), math.floor(raw_hi))
    return raw * signal.scale + signal.offset


class Simulator:
    """Encodes and schedules every encodable message in a DBC."""

    def __init__(self, db: cantools.database.Database):
        self.messages = []
        for message in db.messages:
            if message.is_multiplexed():
                log.warning("skipping multiplexed message %s", message.name)
                continue
            period = (message.cycle_time or 0) / 1000 or DEFAULT_PERIOD
            self.messages.append((message, period))

    def frame(self, message, t: float) -> can.Message:
        values = {s.name: signal_value(s, t) for s in message.signals}
        return can.Message(
            arbitration_id=message.frame_id,
            is_extended_id=message.is_extended_frame,
            is_fd=message.is_fd,
            data=message.encode(values, strict=False),
        )

    def run(self, bus: can.BusABC, stop: threading.Event) -> None:
        """Send every message at its period until `stop` is set."""
        start = time.monotonic()
        due = [0.0] * len(self.messages)
        while not stop.is_set():
            now = time.monotonic() - start
            for i, (message, period) in enumerate(self.messages):
                if now >= due[i]:
                    bus.send(self.frame(message, now))
                    # Schedule from the previous deadline to avoid drift, but
                    # skip missed periods instead of sending a burst.
                    due[i] += period
                    if due[i] < now:
                        due[i] = now + period
            stop.wait(max(0.0, min(due) - (time.monotonic() - start)))
