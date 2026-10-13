from types import SimpleNamespace

from transfer_filters import should_skip


def test_clean_keeps_photo() -> None:
    msg = SimpleNamespace(photo=object(), video=None, service=False, empty=False, media_group_id=None)
    assert should_skip(msg, True) is False


def test_clean_skips_text() -> None:
    msg = SimpleNamespace(photo=None, video=None, service=False, empty=False, media_group_id=None, text="hi")
    assert should_skip(msg, True) is True


def test_clean_off_keeps_text() -> None:
    msg = SimpleNamespace(photo=None, video=None, service=False, empty=False, media_group_id=None, text="hi")
    assert should_skip(msg, False) is False
