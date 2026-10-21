import uuid

import can
import cantools
import pytest

from canscope.cli import demo_dbc_path


@pytest.fixture(scope="session")
def db():
    return cantools.database.load_file(demo_dbc_path())


@pytest.fixture
def channel():
    """A virtual bus channel no other test shares."""
    return f"test-{uuid.uuid4().hex[:8]}"


def frame(db, name, timestamp=0.0, **values):
    """Encode a demo message, filling unspecified signals with their minimum."""
    message = db.get_message_by_name(name)
    signals = {s.name: s.minimum or 0 for s in message.signals}
    signals.update(values)
    return can.Message(
        arbitration_id=message.frame_id,
        is_extended_id=message.is_extended_frame,
        data=message.encode(signals),
        timestamp=timestamp,
    )
