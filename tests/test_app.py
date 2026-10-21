"""Drive the Textual UI headless with a simulated bus."""

import asyncio

import can
from textual.widgets import DataTable, Sparkline, Static

from canscope.app import CanScopeApp, fmt_number, fmt_period
from canscope.sim import Simulator


def make_app(db, channel, **kwargs):
    sim = Simulator(db)
    tx = can.Bus(interface="virtual", channel=channel)
    rx = can.Bus(interface="virtual", channel=channel)
    app = CanScopeApp(rx, db, source_label="test", producer=lambda stop: sim.run(tx, stop), **kwargs)
    return app, (tx, rx)


def run(app, buses, scenario):
    async def main():
        async with app.run_test(size=(140, 36)) as pilot:
            await scenario(app, pilot)

    try:
        asyncio.run(main())
    finally:
        app.close()
        for bus in buses:
            bus.shutdown()


def text(app, selector):
    return str(app.query_one(selector, Static).render())


def test_shows_every_message_sorted_with_extended_last(db, channel):
    async def scenario(app, pilot):
        await pilot.pause(1.2)
        table = app.query_one("#messages", DataTable)
        assert table.row_count == 5
        ids = [table.get_row_at(i)[0] for i in range(table.row_count)]
        assert ids == ["100", "200", "300", "400", "18FEF100"]
        assert "frames" in text(app, "#status")
        assert app.monitor.total_frames > 50

    run(*make_app(db, channel), scenario)


def test_selecting_a_message_shows_its_signals_and_plot(db, channel):
    async def scenario(app, pilot):
        await pilot.pause(1.0)
        await pilot.click("#messages")
        await pilot.press("down")  # VehicleSpeed
        await pilot.pause(0.5)
        signals = app.query_one("#signals", DataTable)
        names = [signals.get_row_at(i)[0] for i in range(signals.row_count)]
        assert names == ["Speed", "Gear", "SteeringAngle"]
        assert signals.get_row_at(1)[3:] == ["-", "-"]  # no min/max for an enumeration

        await pilot.click("#signals")
        await pilot.pause(0.5)
        assert app.selected_signal == "Speed"
        assert len(app.query_one("#plot", Sparkline).data) > 0
        assert "Speed [km/h]" in text(app, "#plot-title")

    run(*make_app(db, channel), scenario)


def test_pause_freezes_table_and_clear_resets(db, channel):
    async def scenario(app, pilot):
        await pilot.pause(0.6)
        await pilot.press("p")
        table = app.query_one("#messages", DataTable)
        frozen = table.get_row_at(0)[2]
        await pilot.pause(0.5)
        assert table.get_row_at(0)[2] == frozen
        assert "PAUSED" in text(app, "#status")

        before = app.monitor.total_frames
        await pilot.press("p", "c")
        assert app.monitor.total_frames < before / 2  # new frames keep arriving after the clear

    run(*make_app(db, channel), scenario)


def test_records_traffic_to_a_log(db, channel, tmp_path):
    log = tmp_path / "out.asc"

    async def scenario(app, pilot):
        await pilot.pause(0.6)

    run(*make_app(db, channel, log_path=str(log)), scenario)
    frames = list(can.LogReader(str(log)))
    assert len(frames) > 20
    assert {f.arbitration_id for f in frames} >= {0x100, 0x200}


def test_formatting():
    assert fmt_number(0.1 + 0.2) == "0.3"
    assert fmt_number(2500.25) == "2500.25"
    assert fmt_number("D") == "D"
    assert fmt_number(None) == "-"
    assert fmt_period(0.02) == "20.0 ms"
    assert fmt_period(None) == "-"
