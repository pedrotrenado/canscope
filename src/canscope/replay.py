"""Replay a recorded log (.asc, .blf, .log, .trc, ...) onto a bus with its original timing."""

from __future__ import annotations

import threading
import time

import can


def replay(path: str, bus: can.BusABC, stop: threading.Event, speed: float = 1.0, loop: bool = False) -> int:
    """Send the frames in `path` to `bus`, `speed` times faster than recorded.

    Returns the number of frames sent. Error frames are skipped.
    """
    if speed <= 0:
        raise ValueError("speed must be positive")
    sent = 0
    while not stop.is_set():
        start = time.monotonic()
        first = None
        with can.LogReader(path) as reader:
            for msg in reader:
                if msg.is_error_frame:
                    continue
                if first is None:
                    first = msg.timestamp
                delay = (msg.timestamp - first) / speed - (time.monotonic() - start)
                if stop.is_set() or (delay > 0 and stop.wait(delay)):
                    return sent
                bus.send(msg)
                sent += 1
        if not loop or first is None:
            break
    return sent
