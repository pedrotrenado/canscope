import threading
import time

import can
import pytest

from canscope.replay import replay


@pytest.fixture
def recording(tmp_path):
    """A 1-second .asc log: 11 frames, 100 ms apart, plus one error frame."""
    path = tmp_path / "trace.asc"
    with can.Logger(path) as writer:
        for i in range(11):
            writer.on_message_received(
                can.Message(
                    arbitration_id=0x100 + i % 3,
                    is_extended_id=False,
                    data=[i, 0xAA],
                    timestamp=100.0 + i * 0.1,
                )
            )
        writer.on_message_received(can.Message(is_error_frame=True, timestamp=100.55))
    return str(path)


def run_replay(recording, channel, **kwargs):
    tx = can.Bus(interface="virtual", channel=channel)
    rx = can.Bus(interface="virtual", channel=channel)
    try:
        start = time.monotonic()
        sent = replay(recording, tx, threading.Event(), **kwargs)
        elapsed = time.monotonic() - start
        received = []
        while (msg := rx.recv(timeout=0)) is not None:
            received.append(msg)
    finally:
        tx.shutdown()
        rx.shutdown()
    return sent, received, elapsed


def test_replays_every_frame_in_order(recording, channel):
    sent, received, _ = run_replay(recording, channel, speed=20)

    assert sent == 11
    assert [m.data[0] for m in received] == list(range(11))
    assert [m.arbitration_id for m in received[:3]] == [0x100, 0x101, 0x102]


def test_keeps_original_timing(recording, channel):
    _, _, elapsed = run_replay(recording, channel, speed=1)
    assert 0.9 <= elapsed <= 1.5


def test_speed_factor(recording, channel):
    _, _, elapsed = run_replay(recording, channel, speed=4)
    assert 0.2 <= elapsed <= 0.5


def test_stop_interrupts_replay(recording, channel):
    tx = can.Bus(interface="virtual", channel=channel)
    stop = threading.Event()
    threading.Timer(0.25, stop.set).start()
    try:
        sent = replay(recording, tx, stop, speed=1, loop=True)
    finally:
        tx.shutdown()
    assert 2 <= sent <= 4


def test_rejects_non_positive_speed(recording, channel):
    with pytest.raises(ValueError):
        replay(recording, None, threading.Event(), speed=0)
