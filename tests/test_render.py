from drei.commands import BufferObservation
from drei.render import render


def obs(text: str, point: int, buffer_id: str = "scratch") -> BufferObservation:
    return BufferObservation(buffer_id, text, point)


def test_empty_buffer_frame() -> None:
    frame = render(obs("", 0), width=10, height=4)
    assert frame.rows == (
        "          ",
        "          ",
        "Drei: scra",
        "          ",
    )
    assert frame.cursor == (0, 0)


def test_inserted_text_frame() -> None:
    frame = render(obs("hello", 5), width=10, height=4)
    assert frame.rows == (
        "hello     ",
        "          ",
        "Drei: scra",
        "          ",
    )
    assert frame.cursor == (0, 5)


def test_multiline_frame() -> None:
    frame = render(obs("ab\ncd", 4), width=10, height=4)
    assert frame.rows == (
        "ab        ",
        "cd        ",
        "Drei: scra",
        "          ",
    )
    # Point 4 is before 'd' on the second line.
    assert frame.cursor == (1, 1)


def test_horizontal_clipping() -> None:
    frame = render(obs("abcdef", 3), width=4, height=3)
    assert frame.rows == (
        "abcd",
        "Drei",
        "    ",
    )
    assert frame.cursor == (0, 3)


def test_height_one_is_modeline_only() -> None:
    frame = render(obs("x", 1), width=10, height=1)
    assert frame.rows == ("Drei: scra",)
    assert frame.cursor == (0, 0)


def test_height_zero() -> None:
    frame = render(obs("x", 1), width=10, height=0)
    assert frame.rows == ()
    assert frame.cursor == (0, 0)


def test_width_zero() -> None:
    frame = render(obs("x", 1), width=0, height=3)
    assert frame.rows == ("", "", "")
    assert frame.cursor == (0, 0)


def test_height_two_has_no_body() -> None:
    frame = render(obs("hello", 5), width=10, height=2)
    assert frame.rows == ("Drei: scra", "          ")
    assert frame.cursor == (0, 0)


def test_echo_area_reflects_quit() -> None:
    frame = render(obs("", 0), width=10, height=4, echo="Quit")
    assert frame.rows == (
        "          ",
        "          ",
        "Drei: scra",
        "Quit      ",
    )
