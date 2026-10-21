import threading
from collections import Counter

import can
import cantools
import pytest

from canscope.sim import Simulator, signal_value

TIMES = [i * 0.37 for i in range(300)]  # ~110 s of simulated time


def test_values_stay_in_dbc_range_and_encode_strictly(db):
    sim = Simulator(db)
    for message, _ in sim.messages:
        for t in TIMES:
            values = {s.name: signal_value(s, t) for s in message.signals}
            # strict=True makes cantools check every value against [min|max].
            message.encode(values, strict=True)


def test_signals_actually_move(db):
    for message in db.messages:
        for signal in message.signals:
            assert len({signal_value(signal, t) for t in TIMES}) > 1, signal.name


def test_values_are_deterministic(db):
    signal = db.get_message_by_name("EngineData").get_signal_by_name("EngineSpeed")
    assert signal_value(signal, 12.5) == signal_value(signal, 12.5)


def test_choice_signals_only_use_defined_values(db):
    gear = db.get_message_by_name("VehicleSpeed").get_signal_by_name("Gear")
    assert {signal_value(gear, t) for t in TIMES} == set(gear.choices)


def test_one_bit_signals_toggle(db):
    signal = db.get_message_by_name("BodyLights").get_signal_by_name("LeftIndicator")
    assert {signal_value(signal, t) for t in TIMES} == {0.0, 1.0}


def test_dbc_range_larger_than_raw_field_is_clamped():
    db = cantools.database.load_string(
        'VERSION ""\nBU_: A\nBO_ 1 M: 1 A\n SG_ S : 0|8@1+ (1,0) [0|300] "" A\n', "dbc"
    )
    message = db.get_message_by_name("M")
    signal = message.get_signal_by_name("S")
    values = [signal_value(signal, t) for t in TIMES]
    assert max(values) == pytest.approx(255, abs=5)
    assert all(0 <= v <= 255 for v in values)


def test_signal_without_range_uses_raw_field():
    db = cantools.database.load_string(
        'VERSION ""\nBU_: A\nBO_ 1 M: 2 A\n SG_ S : 0|12@1- (0.5,0) [0|0] "" A\n', "dbc"
    )
    signal = db.get_message_by_name("M").get_signal_by_name("S")
    values = [signal_value(signal, t) for t in TIMES]
    assert min(values) < -500 and max(values) > 500
    assert all(-1024 <= v <= 1023.5 for v in values)


def test_run_sends_each_message_at_its_cycle_time(db, channel):
    sim = Simulator(db)
    tx = can.Bus(interface="virtual", channel=channel)
    rx = can.Bus(interface="virtual", channel=channel)
    stop = threading.Event()
    thread = threading.Thread(target=sim.run, args=(tx, stop))
    try:
        thread.start()
        threading.Event().wait(1.0)
        stop.set()
        thread.join()
        received = []
        while (msg := rx.recv(timeout=0)) is not None:
            received.append(msg)
    finally:
        tx.shutdown()
        rx.shutdown()

    counts = Counter(m.arbitration_id for m in received)
    assert 35 <= counts[0x100] <= 55  # every 20 ms
    assert 15 <= counts[0x200] <= 23  # every 50 ms
    assert 2 <= counts[0x18FEF100] <= 3  # every 500 ms
    cruise = next(m for m in received if m.arbitration_id == 0x18FEF100)
    engine = next(m for m in received if m.arbitration_id == 0x100)
    assert cruise.is_extended_id and not engine.is_extended_id
    assert len(engine.data) == 8
