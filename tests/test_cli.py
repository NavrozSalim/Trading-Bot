from __future__ import annotations

from trading_bot.__main__ import parse_args


def test_parse_setup_flag() -> None:
    args = parse_args(["--setup"])
    assert args.setup is True
    assert args.run is False


def test_parse_test_selectors_flag() -> None:
    args = parse_args(["--test-selectors"])
    assert args.test_selectors is True


def test_parse_copy_chrome_profile_flag() -> None:
    args = parse_args(["--copy-chrome-profile"])
    assert args.copy_chrome_profile is True
