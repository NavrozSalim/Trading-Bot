from __future__ import annotations

from pathlib import Path

import pytest

from trading_bot.browser.chrome_profile import (
    assert_chrome_unlocked,
    assert_named_profile_exists,
    copy_named_profile,
    cdp_endpoint,
    find_chrome_executable,
    named_profile_path,
)
from trading_bot.exceptions import ConfigurationError


def test_named_profile_path() -> None:
    root = Path("C:/Users/Navroz/AppData/Local/Google/Chrome/User Data")
    assert named_profile_path(root, "Profile 52") == root / "Profile 52"


def test_missing_profile_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        assert_named_profile_exists(tmp_path, "Profile 52")


def test_existing_profile_ok(tmp_path: Path) -> None:
    folder = tmp_path / "Profile 52"
    folder.mkdir()
    assert assert_named_profile_exists(tmp_path, "Profile 52") == folder


def test_chrome_running_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trading_bot.browser.chrome_profile.chrome_process_running",
        lambda: True,
    )
    with pytest.raises(ConfigurationError, match="already running"):
        assert_chrome_unlocked(require_closed=True)
    assert_chrome_unlocked(require_closed=False)


def test_cdp_endpoint() -> None:
    assert cdp_endpoint(9222) == "http://127.0.0.1:9222"


def test_find_chrome_override(tmp_path: Path) -> None:
    fake = tmp_path / "chrome.exe"
    fake.write_bytes(b"")
    assert find_chrome_executable(str(fake)) == fake


def test_find_chrome_override_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="CHROME_PATH"):
        find_chrome_executable(str(tmp_path / "missing.exe"))


def test_copy_named_profile(tmp_path: Path) -> None:
    source = tmp_path / "src"
    dest = tmp_path / "dst"
    profile = source / "Profile 52"
    (profile / "Network").mkdir(parents=True)
    (profile / "Network" / "Cookies").write_text("cookie", encoding="utf-8")
    (profile / "Cache").mkdir()
    (profile / "Cache" / "big").write_text("skip", encoding="utf-8")
    (source / "Local State").write_text("{}", encoding="utf-8")
    copied = copy_named_profile(source, dest, "Profile 52")
    assert copied.exists()
    assert (copied / "Network" / "Cookies").read_text(encoding="utf-8") == "cookie"
    assert not (copied / "Cache").exists()
    assert (dest / "Local State").exists()


def test_copy_refuses_same_folder(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="same"):
        copy_named_profile(tmp_path, tmp_path, "Profile 52")
