from types import SimpleNamespace

from bot_editor import _strip_html, caption_key
from formatting import apply_caption_template, compose_dest_caption, pyrogram_to_html


def test_pyrogram_caption_html() -> None:
    msg = SimpleNamespace(caption=SimpleNamespace(html="<b>привет</b>"), text=None)
    assert pyrogram_to_html(msg) == "<b>привет</b>"


def test_pyrogram_text_fallback() -> None:
    msg = SimpleNamespace(caption=None, text=SimpleNamespace(html="<i>раз</i>"))
    assert pyrogram_to_html(msg) == "<i>раз</i>"


def test_template_keeps_original_html() -> None:
    assert apply_caption_template("канал\n{text}", "<b>пост</b>") == "канал\n<b>пост</b>"


def test_compose_prefers_source_when_no_template() -> None:
    msg = SimpleNamespace(caption=SimpleNamespace(html='<a href="https://t.me">x</a>'), text=None)
    assert compose_dest_caption("", msg) == '<a href="https://t.me">x</a>'


def test_compose_template_only() -> None:
    assert compose_dest_caption("<b>бренд</b>", SimpleNamespace(caption=None, text=None)) == "<b>бренд</b>"


def test_strip_html_keeps_plain_text() -> None:
    assert _strip_html('<b>VPN</b> <a href="https://t.me/x">shop</a>') == "VPN shop"


def test_caption_key_ignores_nested_tags() -> None:
    nested = '<i>🌐 </i><a href="https://t.me/x"><i>VPN</i></a><i> click</i>'
    stored = '🌐 <a href="https://t.me/x">VPN</a> click'
    assert caption_key(nested) == caption_key(stored)
