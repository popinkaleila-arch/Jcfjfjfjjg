from types import SimpleNamespace

from links import format_post_link, forwarded_point, preview_caption


def test_format_private_link() -> None:
    assert format_post_link("-10035839961", 123) == "https://t.me/c/35839961/123"


def test_format_public_link() -> None:
    assert format_post_link("@channel", 9) == "https://t.me/channel/9"


def test_preview_empty() -> None:
    assert preview_caption("") == "без подписи"


def test_forwarded_channel() -> None:
    message = SimpleNamespace(
        forward_origin=SimpleNamespace(
            chat=SimpleNamespace(username="dest", id=-1001),
            message_id=77,
        ),
        forward_from_chat=None,
        forward_from_message_id=None,
    )
    point = forwarded_point(message)
    assert point is not None
    assert point.chat == "@dest"
    assert point.message_id == 77


def test_forwarded_legacy() -> None:
    message = SimpleNamespace(
        forward_origin=None,
        forward_from_chat=SimpleNamespace(username=None, id=-1003702880360),
        forward_from_message_id=15,
    )
    point = forwarded_point(message)
    assert point is not None
    assert point.chat == "-1003702880360"
    assert point.message_id == 15
