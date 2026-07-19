import pytest

from drei.model import Buffer, BufferId, BufferValue


def test_buffer_value_rejects_negative_point() -> None:
    with pytest.raises(ValueError, match="point"):
        BufferValue(text="", point=-1)


def test_buffer_value_rejects_point_past_end() -> None:
    with pytest.raises(ValueError, match="point"):
        BufferValue(text="abc", point=4)


def test_buffer_shell_identity_is_stable() -> None:
    shell = Buffer(BufferId("scratch"), BufferValue(text="", point=0))
    first_value = shell.current
    shell.replace(BufferValue(text="x", point=1))
    assert shell.current is not first_value
    assert shell.current.text == "x"
    assert first_value.text == ""


def test_buffer_id_structural_equality() -> None:
    assert BufferId("a") == BufferId("a")
    assert BufferId("a") != BufferId("b")


def test_buffer_id_is_read_only() -> None:
    shell = Buffer(BufferId("scratch"), BufferValue(text="", point=0))
    with pytest.raises(AttributeError):
        shell.buffer_id = BufferId("other")  # type: ignore[misc]
