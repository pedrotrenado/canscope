"""Terminal UI: message table, decoded signals and a live plot of one signal."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

import can
import cantools
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import DataTable, Footer, Header, Sparkline, Static

from .model import BusMonitor, MessageState, SignalState

Producer = Callable[[threading.Event], object]

# (label, key, width). Cells are updated in place, which does not grow a
# column, so columns whose content changes get a fixed width up front.
MESSAGE_COLUMNS = [
    ("ID", "id", 8),
    ("Name", "name", None),
    ("Count", "count", 8),
    ("Period", "period", 9),
    ("Data", "data", None),
]
SIGNAL_COLUMNS = [
    ("Signal", "signal", None),
    ("Value", "value", 9),
    ("Unit", "unit", None),
    ("Min", "min", 9),
    ("Max", "max", 9),
]


def fmt_number(value: float | str | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    # .10g hides float noise like 0.30000000000000004 from scaled values.
    return f"{value:.10g}"


def fmt_range(sig: SignalState) -> tuple[str, str]:
    """Min and max for display; meaningless for enumerated signals."""
    if isinstance(sig.value, str):
        return "-", "-"
    return fmt_number(sig.minimum), fmt_number(sig.maximum)


def fmt_period(period: float | None) -> str:
    return "-" if period is None else f"{period * 1000:.1f} ms"


def row_key(state: MessageState) -> str:
    return f"{state.arbitration_id}:{int(state.is_extended)}"


class CanScopeApp(App):
    TITLE = "canscope"
    CSS = """
    #messages { width: 1fr; }
    #side { width: 58; }
    #signals { height: auto; max-height: 60%; }
    #plot-title { height: 1; padding: 0 1; margin-top: 1; color: $text-muted; }
    #plot { height: 1fr; min-height: 6; margin: 0 1 1 1; }
    #status { height: 1; padding: 0 1; background: $panel; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("p", "toggle_pause", "Pause"),
        ("c", "clear", "Clear"),
    ]

    def __init__(
        self,
        bus: can.BusABC,
        db: cantools.database.Database | None = None,
        *,
        source_label: str = "",
        producer: Producer | None = None,
        log_path: str | None = None,
    ):
        super().__init__()
        self.bus = bus
        self.monitor = BusMonitor(db)
        self.source_label = source_label
        self.producer = producer
        self.log_path = log_path
        self.paused = False
        self.rate = 0.0
        self.selected_message: str | None = None
        self.selected_signal: str | None = None
        self._queue: queue.SimpleQueue[can.Message] = queue.SimpleQueue()
        self._dirty: set[str] = set()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._notifier: can.Notifier | None = None
        self._can_logger: can.Listener | None = None
        self._rate_mark = (time.monotonic(), 0)

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        with Horizontal():
            yield DataTable(id="messages", cursor_type="row", zebra_stripes=True)
            with Vertical(id="side"):
                yield DataTable(id="signals", cursor_type="row")
                yield Static(id="plot-title")
                yield Sparkline([], id="plot")
        yield Footer()

    def on_mount(self) -> None:
        for table_id, columns in (("#messages", MESSAGE_COLUMNS), ("#signals", SIGNAL_COLUMNS)):
            table = self.query_one(table_id, DataTable)
            for label, key, width in columns:
                table.add_column(label, key=key, width=width)

        listeners: list = [self._queue.put]
        if self.log_path:
            self._can_logger = can.Logger(self.log_path)
            listeners.append(self._can_logger)
        self._notifier = can.Notifier(self.bus, listeners, timeout=0.1)

        if self.producer is not None:
            thread = threading.Thread(target=self.producer, args=(self._stop,), daemon=True)
            thread.start()
            self._threads.append(thread)

        self.set_interval(0.1, self.drain)
        self.set_interval(1.0, self.update_rate)
        self.update_status()

    def on_unmount(self) -> None:
        self.close()

    def close(self) -> None:
        """Stop the producer, the notifier and the logger. Safe to call twice."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads.clear()
        if self._notifier is not None:
            self._notifier.stop()
            self._notifier = None
        if self._can_logger is not None:
            self._can_logger.stop()
            self._can_logger = None

    # --- data flow ----------------------------------------------------------

    def drain(self) -> None:
        """Move received frames into the model, then refresh what changed."""
        while True:
            try:
                msg = self._queue.get_nowait()
            except queue.Empty:
                break
            state = self.monitor.process(msg)
            if state is not None:
                self._dirty.add(row_key(state))
        try:
            if not self.paused:
                self.refresh_tables()
            self.update_status()
        except NoMatches:
            # The timer can fire once more while the app shuts down and its
            # widgets are already gone; there is nothing left to refresh.
            pass

    def refresh_tables(self) -> None:
        messages = self.query_one("#messages", DataTable)
        states = {row_key(s): s for s in self.monitor.messages.values()}

        if len(states) != messages.row_count:
            self._rebuild_messages(messages, states)
        else:
            for key in self._dirty:
                state = states[key]
                messages.update_cell(key, "count", f"{state.count:,}")
                messages.update_cell(key, "period", fmt_period(state.period))
                messages.update_cell(key, "data", state.data.hex(" ").upper())

        if self.selected_message in self._dirty:
            self._refresh_signals(states[self.selected_message])
        self._dirty.clear()

    def _rebuild_messages(self, table: DataTable, states: dict[str, MessageState]) -> None:
        """New IDs are rare, so keep the table sorted by rebuilding it."""
        table.clear()
        for key, state in sorted(states.items(), key=lambda kv: kv[1].key[::-1]):
            table.add_row(
                state.id_text,
                state.name or "?",
                f"{state.count:,}",
                fmt_period(state.period),
                state.data.hex(" ").upper(),
                key=key,
            )
        if self.selected_message in states:
            table.move_cursor(row=table.get_row_index(self.selected_message))

    def _refresh_signals(self, state: MessageState, rebuild: bool = False) -> None:
        table = self.query_one("#signals", DataTable)
        if rebuild or table.row_count != len(state.signals):
            table.clear()
            for name, sig in state.signals.items():
                table.add_row(
                    name,
                    fmt_number(sig.value),
                    sig.unit,
                    *fmt_range(sig),
                    key=name,
                )
        else:
            for name, sig in state.signals.items():
                low, high = fmt_range(sig)
                table.update_cell(name, "value", fmt_number(sig.value))
                table.update_cell(name, "min", low)
                table.update_cell(name, "max", high)
        self._refresh_plot(state)

    def _refresh_plot(self, state: MessageState | None) -> None:
        title = self.query_one("#plot-title", Static)
        plot = self.query_one("#plot", Sparkline)
        sig = state.signals.get(self.selected_signal) if state and self.selected_signal else None
        if sig is None:
            title.update("Select a signal to plot it")
            plot.data = []
            return
        unit = f" [{sig.unit}]" if sig.unit else ""
        low, high = fmt_range(sig)
        title.update(f"{sig.name}{unit}   min {low}   max {high}")
        plot.data = list(sig.history)

    def update_rate(self) -> None:
        now, total = time.monotonic(), self.monitor.total_frames
        then, before = self._rate_mark
        self.rate = (total - before) / (now - then) if now > then else 0.0
        self._rate_mark = (now, total)

    def update_status(self) -> None:
        m = self.monitor
        parts = [
            self.source_label,
            f"{m.total_frames:,} frames",
            f"{self.rate:,.0f} fps",
            f"{len(m.messages)} IDs",
        ]
        if m.error_frames:
            parts.append(f"{m.error_frames} error frames")
        if self.log_path:
            parts.append(f"logging to {self.log_path}")
        if self.paused:
            parts.append("PAUSED")
        self.query_one("#status", Static).update("  ·  ".join(p for p in parts if p))

    # --- events and actions -------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value
        if event.data_table.id == "messages":
            if key != self.selected_message:
                self.selected_message = key
                self.selected_signal = None
                state = self._state(key)
                if state is not None:
                    self._refresh_signals(state, rebuild=True)
        elif event.data_table.id == "signals":
            self.selected_signal = key
            self._refresh_plot(self._state(self.selected_message))

    def _state(self, key: str | None) -> MessageState | None:
        if key is None:
            return None
        arbitration_id, extended = key.split(":")
        return self.monitor.messages.get((int(arbitration_id), extended == "1"))

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.update_status()

    def action_clear(self) -> None:
        self.monitor.clear()
        self._dirty.clear()
        self.selected_message = self.selected_signal = None
        self.query_one("#messages", DataTable).clear()
        self.query_one("#signals", DataTable).clear()
        self._refresh_plot(None)
        self.update_status()
