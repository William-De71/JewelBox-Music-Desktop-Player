import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.device import ensure_device_id  # noqa: E402


def test_existing_id_is_kept():
    assert ensure_device_id('abc-123') == 'abc-123'


def test_existing_id_is_trimmed_not_replaced():
    assert ensure_device_id('  abc-123  ') == 'abc-123'


def test_empty_generates_uuid():
    generated = ensure_device_id('')
    assert str(uuid.UUID(generated)) == generated


def test_none_generates_uuid():
    generated = ensure_device_id(None)
    assert str(uuid.UUID(generated)) == generated


def test_whitespace_only_generates_uuid():
    generated = ensure_device_id('   ')
    assert str(uuid.UUID(generated)) == generated


def test_two_generations_differ():
    assert ensure_device_id('') != ensure_device_id('')
