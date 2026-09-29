from __future__ import annotations

from pathlib import Path

import pytest

from trading_bot.config import Settings, clear_settings_cache


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    clear_settings_cache()
    return Settings(
        _env_file=None,
        trading_mode="DEMO",
        allow_live_trading="",
        dry_run=True,
        headless=True,
        broker_url="https://example-broker.invalid/trade",
        symbol="BTCUSD",
        timeframe="5M",
        browser_profile_dir=tmp_path / "profile",
        screenshots_dir=tmp_path / "shots",
        logs_dir=tmp_path / "logs",
        selectors_file=tmp_path / "selectors.yaml",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'bot.db').as_posix()}",
    )


class FakeLocator:
    def __init__(
        self,
        *,
        visible: bool = False,
        enabled: bool = True,
        count: int = 0,
        text: str = "",
        fail_wait: bool = False,
    ) -> None:
        self._visible = visible
        self._enabled = enabled
        self._count = count if count or not visible else 1
        if visible and self._count == 0:
            self._count = 1
        self._text = text
        self._fail_wait = fail_wait
        self.first = self
        self.fills: list[str] = []
        self.clicks = 0

    async def is_visible(self) -> bool:
        return self._visible

    async def is_enabled(self) -> bool:
        return self._enabled

    async def count(self) -> int:
        return self._count

    async def inner_text(self) -> str:
        return self._text

    async def fill(self, value: str) -> None:
        self.fills.append(value)

    async def click(self) -> None:
        self.clicks += 1

    async def wait_for(self, **_kwargs: object) -> None:
        if self._fail_wait or not self._visible:
            raise TimeoutError("element not visible")

    async def evaluate(self, *_args: object, **_kwargs: object) -> None:
        return None


class FakePage:
    def __init__(self, locators: dict[str, FakeLocator] | None = None, url: str = "https://example-broker.invalid/trade") -> None:
        self._locators = locators or {}
        self.url = url
        self.goto_calls: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        return self._locators.get(selector, FakeLocator(visible=False, count=0))

    async def title(self) -> str:
        return "Demo broker"

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.url = url
        self.goto_calls.append(url)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def evaluate(self, _expr: str) -> object:
        return None

    async def screenshot(self, path: str, full_page: bool = False) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
