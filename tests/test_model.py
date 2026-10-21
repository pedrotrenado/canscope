import can
import pytest
from conftest import frame

from canscope.model import BusMonitor


def test_decodes_known_message(db):
    monitor = BusMonitor(db)
    state = monitor.process(frame(db, "EngineData", EngineSpeed=2500.25, EngineTemp=90))

    assert state.name == "EngineData"
    assert state.id_text == "100"
    assert state.signals["EngineSpeed"].value == 2500.25
    assert state.signals["EngineSpeed"].unit == "rpm"
    assert state.signals["EngineTemp"].value == 90
    assert state.decode_error is None


def test_choice_signals_show_name_and_plot_number(db):
    monitor = BusMonitor(db)
    state = monitor.process(frame(db, "VehicleSpeed", Gear="D"))

    gear = state.signals["Gear"]
    assert gear.value == "D"
    assert list(gear.history) == [3.0]


def test_extended_id(db):
    state = BusMonitor(db).process(frame(db, "CruiseControl", SetSpeed=120))

    assert state.name == "CruiseControl"
    assert state.id_text == "18FEF100"
    assert state.signals["SetSpeed"].value == 120


def test_unknown_id_is_tracked_but_not_decoded(db):
    monitor = BusMonitor(db)
    state = monitor.process(can.Message(arbitration_id=0x7DF, is_extended_id=False, data=[2, 1, 0]))

    assert state.name is None
    assert state.signals == {}
    assert state.data == bytes([2, 1, 0])
    assert monitor.total_frames == 1


def test_standard_and_extended_frames_with_same_number_are_different(db):
    monitor = BusMonitor(db)
    standard = frame(db, "EngineData")
    extended = can.Message(arbitration_id=standard.arbitration_id, is_extended_id=True, data=standard.data)

    assert monitor.process(standard).name == "EngineData"
    assert monitor.process(extended).name is None
    assert len(monitor.messages) == 2


def test_works_without_a_dbc():
    state = BusMonitor().process(can.Message(arbitration_id=0x123, is_extended_id=False, data=[1]))
    assert state.name is None


def test_period_is_estimated_from_timestamps(db):
    monitor = BusMonitor(db)
    for i in range(20):
        state = monitor.process(frame(db, "EngineData", timestamp=i * 0.02))

    assert state.count == 20
    assert state.period == pytest.approx(0.02)


def test_period_ignores_out_of_order_timestamps(db):
    monitor = BusMonitor(db)
    monitor.process(frame(db, "EngineData", timestamp=1.0))
    state = monitor.process(frame(db, "EngineData", timestamp=0.5))
    assert state.period is None


def test_min_max_and_bounded_history(db):
    monitor = BusMonitor(db, history=5)
    for rpm in [1000, 3000, 500, 2000, 2000, 2000, 2000]:
        state = monitor.process(frame(db, "EngineData", EngineSpeed=rpm))

    rpm = state.signals["EngineSpeed"]
    assert (rpm.minimum, rpm.maximum) == (500, 3000)
    assert list(rpm.history) == [500, 2000, 2000, 2000, 2000]


def test_bad_frame_records_decode_error_and_recovers(db):
    monitor = BusMonitor(db)
    short = can.Message(arbitration_id=0x100, is_extended_id=False, data=[1, 2])

    state = monitor.process(short)
    assert state.decode_error
    assert state.count == 1

    state = monitor.process(frame(db, "EngineData"))
    assert state.decode_error is None


def test_error_frames_are_counted_separately(db):
    monitor = BusMonitor(db)
    assert monitor.process(can.Message(is_error_frame=True)) is None
    assert monitor.error_frames == 1
    assert monitor.total_frames == 0
    assert monitor.messages == {}


def test_clear(db):
    monitor = BusMonitor(db)
    monitor.process(frame(db, "EngineData"))
    monitor.clear()
    assert (monitor.messages, monitor.total_frames) == ({}, 0)
