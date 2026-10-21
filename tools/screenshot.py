"""Run the demo headless and save a screenshot for the README.

python tools/screenshot.py docs/screenshot.svg
"""

import asyncio
import sys

import cantools

from canscope.app import CanScopeApp
from canscope.cli import demo_dbc_path, virtual_pair
from canscope.sim import Simulator


async def capture(path: str) -> None:
    db = cantools.database.load_file(demo_dbc_path())
    sim = Simulator(db)
    tx, rx = virtual_pair()
    app = CanScopeApp(rx, db, source_label="demo: simulated vehicle", producer=lambda stop: sim.run(tx, stop))
    try:
        async with app.run_test(size=(130, 32)) as pilot:
            await pilot.pause(2.0)
            await pilot.click("#messages")
            await pilot.press("down")  # VehicleSpeed
            await pilot.click("#signals")
            await pilot.pause(6.0)  # let the plot fill up
            app.update_rate()
            await pilot.pause(0.2)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(app.export_screenshot(title="canscope demo"))
    finally:
        app.close()
        for bus in (tx, rx):
            bus.shutdown()


def main() -> None:
    asyncio.run(capture(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshot.svg"))


if __name__ == "__main__":
    main()
