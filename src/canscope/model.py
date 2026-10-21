"""Bus state: per-message statistics and decoded signals, independent of the UI."""

from __future__ import annotations

import contextlib
from collections import deque
from dataclasses import dataclass, field

import can
import cantools

# Weight of the newest inter-arrival time in the period estimate.
PERIOD_SMOOTHING = 0.2


@dataclass
class SignalState:
    name: str
    unit: str
    value: float | str | None = None  # choice name for enumerated signals
    minimum: float | None = None
    maximum: float | None = None
    history: deque[float] = field(default_factory=deque)

    def update(self, value: float | str, numeric: float | None) -> None:
        self.value = value
        if numeric is None:
            return
        self.history.append(numeric)
        self.minimum = numeric if self.minimum is None else min(self.minimum, numeric)
        self.maximum = numeric if self.maximum is None else max(self.maximum, numeric)


@dataclass
class MessageState:
    arbitration_id: int
    is_extended: bool
    name: str | None
    data: bytes = b""
    count: int = 0
    last_timestamp: float | None = None
    period: float | None = None  # smoothed inter-arrival time, seconds
    signals: dict[str, SignalState] = field(default_factory=dict)
    decode_error: str | None = None

    @property
    def key(self) -> tuple[int, bool]:
        return (self.arbitration_id, self.is_extended)

    @property
    def id_text(self) -> str:
        return f"{self.arbitration_id:08X}" if self.is_extended else f"{self.arbitration_id:03X}"


class BusMonitor:
    """Tracks every message seen on a bus and decodes it with an optional DBC."""

    def __init__(self, db: cantools.database.Database | None = None, history: int = 200):
        self.db = db
        self.history = history
        self.messages: dict[tuple[int, bool], MessageState] = {}
        self.total_frames = 0
        self.error_frames = 0
        self._definitions: dict[tuple[int, bool], cantools.database.Message | None] = {}

    def clear(self) -> None:
        self.messages.clear()
        self.total_frames = 0
        self.error_frames = 0

    def process(self, msg: can.Message) -> MessageState | None:
        if msg.is_error_frame:
            self.error_frames += 1
            return None
        self.total_frames += 1

        key = (msg.arbitration_id, msg.is_extended_id)
        definition = self._definition(key)
        state = self.messages.get(key)
        if state is None:
            state = MessageState(
                msg.arbitration_id, msg.is_extended_id, definition.name if definition else None
            )
            self.messages[key] = state

        if state.last_timestamp is not None and msg.timestamp > state.last_timestamp:
            dt = msg.timestamp - state.last_timestamp
            state.period = (
                dt
                if state.period is None
                else ((1 - PERIOD_SMOOTHING) * state.period + PERIOD_SMOOTHING * dt)
            )
        state.last_timestamp = msg.timestamp
        state.count += 1
        state.data = bytes(msg.data)

        if definition is not None:
            self._decode(state, definition, msg)
        return state

    def _definition(self, key: tuple[int, bool]) -> cantools.database.Message | None:
        if key not in self._definitions:
            definition = None
            if self.db is not None:
                with contextlib.suppress(KeyError):
                    definition = self.db.get_message_by_frame_id(key[0])
            # A standard and an extended frame can share the same numeric ID.
            if definition is not None and definition.is_extended_frame != key[1]:
                definition = None
            self._definitions[key] = definition
        return self._definitions[key]

    def _decode(self, state: MessageState, definition: cantools.database.Message, msg: can.Message) -> None:
        try:
            values = definition.decode(bytes(msg.data), decode_choices=True)
        except Exception as exc:  # cantools raises several error types on bad frames
            state.decode_error = str(exc)
            return
        state.decode_error = None

        for signal in definition.signals:
            if signal.name not in values:  # e.g. multiplexed out
                continue
            sig = state.signals.get(signal.name)
            if sig is None:
                sig = SignalState(signal.name, signal.unit or "", history=deque(maxlen=self.history))
                state.signals[signal.name] = sig
            value = values[signal.name]
            if isinstance(value, (int, float)):
                sig.update(value, float(value))
            else:  # NamedSignalValue: show the name, plot the number
                sig.update(str(value), float(value.value))
