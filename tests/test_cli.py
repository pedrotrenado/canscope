import can
import pytest

from canscope.cli import build_parser, demo_dbc_path, main


def test_demo_dbc_is_packaged():
    assert demo_dbc_path().endswith("demo_vehicle.dbc")


def test_parser_defaults():
    args = build_parser().parse_args(["monitor", "--dbc", "x.dbc"])
    assert (args.interface, args.channel, args.bitrate) == ("virtual", "canscope", None)

    args = build_parser().parse_args(["replay", "trace.asc", "--speed", "2", "--loop"])
    assert (args.logfile, args.speed, args.loop) == ("trace.asc", 2.0, True)


def test_command_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_simulate_sends_on_the_chosen_bus(channel, capsys):
    rx = can.Bus(interface="virtual", channel=channel)
    try:
        main(["simulate", "--dbc", demo_dbc_path(), "--duration", "0.3", "-c", channel])
        received = []
        while (msg := rx.recv(timeout=0)) is not None:
            received.append(msg)
    finally:
        rx.shutdown()

    assert "Sending 5 messages" in capsys.readouterr().out
    assert len({m.arbitration_id for m in received}) == 5
