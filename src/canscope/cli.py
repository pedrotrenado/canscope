"""Command line entry point: canscope demo | monitor | simulate | replay."""

from __future__ import annotations

import argparse
import threading
import uuid
from importlib import resources

import can
import cantools

from . import __version__
from .replay import replay
from .sim import Simulator


def demo_dbc_path() -> str:
    return str(resources.files("canscope.data") / "demo_vehicle.dbc")


def load_dbc(path: str | None) -> cantools.database.Database | None:
    return cantools.database.load_file(path) if path else None


def open_bus(args: argparse.Namespace) -> can.BusABC:
    kwargs = {"interface": args.interface, "channel": args.channel}
    if args.bitrate:
        kwargs["bitrate"] = args.bitrate
    return can.Bus(**kwargs)


def virtual_pair() -> tuple[can.BusABC, can.BusABC]:
    """Two ends of a private in-process virtual bus: one to send, one to monitor."""
    channel = f"canscope-{uuid.uuid4().hex[:8]}"
    return can.Bus(interface="virtual", channel=channel), can.Bus(interface="virtual", channel=channel)


def run_app(bus: can.BusABC, db, label: str, producer=None, log_path: str | None = None) -> None:
    from .app import CanScopeApp  # Textual is only needed for the UI commands

    app = CanScopeApp(bus, db, source_label=label, producer=producer, log_path=log_path)
    try:
        app.run()
    finally:
        app.close()


def cmd_demo(args: argparse.Namespace) -> None:
    db = cantools.database.load_file(demo_dbc_path())
    sim = Simulator(db)
    tx, rx = virtual_pair()
    try:
        run_app(rx, db, "demo: simulated vehicle", lambda stop: sim.run(tx, stop), args.log)
    finally:
        tx.shutdown()
        rx.shutdown()


def cmd_monitor(args: argparse.Namespace) -> None:
    bus = open_bus(args)
    try:
        run_app(bus, load_dbc(args.dbc), f"{args.interface}:{args.channel}", log_path=args.log)
    finally:
        bus.shutdown()


def cmd_simulate(args: argparse.Namespace) -> None:
    sim = Simulator(cantools.database.load_file(args.dbc))
    bus = open_bus(args)
    stop = threading.Event()
    if args.duration:
        timer = threading.Timer(args.duration, stop.set)
        timer.daemon = True
        timer.start()
    print(f"Sending {len(sim.messages)} messages on {args.interface}:{args.channel}. Ctrl-C to stop.")
    try:
        sim.run(bus, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        bus.shutdown()


def cmd_replay(args: argparse.Namespace) -> None:
    tx, rx = virtual_pair()

    def producer(stop: threading.Event) -> None:
        replay(args.logfile, tx, stop, speed=args.speed, loop=args.loop)

    try:
        run_app(rx, load_dbc(args.dbc), f"replay: {args.logfile} x{args.speed:g}", producer)
    finally:
        tx.shutdown()
        rx.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canscope", description="Terminal CAN bus analyzer.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def bus_options(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "-i",
            "--interface",
            default="virtual",
            help="python-can interface: virtual, slcan, pcan, socketcan, ... (default: virtual)",
        )
        p.add_argument(
            "-c",
            "--channel",
            default="canscope",
            help="interface channel, e.g. COM5, PCAN_USBBUS1, can0 (default: canscope)",
        )
        p.add_argument("-b", "--bitrate", type=int, help="bitrate in bit/s, e.g. 500000")

    p = sub.add_parser("demo", help="simulated vehicle on a private virtual bus, no hardware needed")
    p.add_argument("--log", help="also record traffic to this file (.asc, .blf, .log, ...)")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("monitor", help="watch a real or virtual bus")
    p.add_argument("--dbc", help="DBC file used to decode signals")
    p.add_argument("--log", help="also record traffic to this file (.asc, .blf, .log, ...)")
    bus_options(p)
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("simulate", help="send simulated traffic for every message in a DBC")
    p.add_argument("--dbc", required=True, help="DBC file describing the messages to send")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    bus_options(p)
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("replay", help="replay a recorded log and watch it")
    p.add_argument("logfile", help="recorded traffic (.asc, .blf, .log, .trc, ...)")
    p.add_argument("--dbc", help="DBC file used to decode signals")
    p.add_argument("--speed", type=float, default=1.0, help="playback speed factor (default: 1)")
    p.add_argument("--loop", action="store_true", help="start again at the end of the log")
    p.set_defaults(func=cmd_replay)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)
