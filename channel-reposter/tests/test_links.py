from links import format_duration, normalize_chat, parse_duration, parse_start_link


def test_normalize_username() -> None:
    assert normalize_chat("durov") == "@durov"
    assert normalize_chat("@channel") == "@channel"


def test_normalize_private_id() -> None:
    assert normalize_chat("35839961") == "-10035839961"
    assert normalize_chat("-10035839961") == "-10035839961"
    assert normalize_chat("t.me/c/35839961/10") == "-10035839961"


def test_saved_messages() -> None:
    assert normalize_chat("избранное") == "me"
    assert normalize_chat("me") == "me"


def test_start_link() -> None:
    point = parse_start_link("https://t.me/c/35839961/123")
    assert point.chat == "-10035839961"
    assert point.message_id == 123


def test_duration() -> None:
    assert parse_duration("30м") == 1800
    assert parse_duration("1д") == 86400
    assert parse_duration("2ч") == 7200
    assert format_duration(86400) == "1 д"
