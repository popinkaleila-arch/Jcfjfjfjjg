from types import SimpleNamespace

from formatting import media_fingerprint


def test_photo_fingerprint() -> None:
    msg = SimpleNamespace(
        photo=SimpleNamespace(file_unique_id="abc"),
        video=None,
        animation=None,
        document=None,
    )
    assert media_fingerprint(msg) == "p:abc"


def test_video_fingerprint() -> None:
    msg = SimpleNamespace(
        photo=None,
        video=SimpleNamespace(file_unique_id="vid1"),
        animation=None,
        document=None,
    )
    assert media_fingerprint(msg) == "v:vid1"


def test_no_media() -> None:
    msg = SimpleNamespace(photo=None, video=None, animation=None, document=None)
    assert media_fingerprint(msg) is None
