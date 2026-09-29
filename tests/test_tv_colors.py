from trading_bot.market.tv_colors import colors_from_rows


def _blank(width: int, height: int) -> list[list[tuple[int, int, int]]]:
    return [[(255, 255, 255) for _ in range(width)] for _ in range(height)]


def _paint(rows: list[list[tuple[int, int, int]]], x0: int, x1: int, color: tuple[int, int, int]) -> None:
    for y in range(10, 30):
        for x in range(x0, x1):
            rows[y][x] = color


def test_rightmost_closed_candle_color_wins() -> None:
    rows = _blank(24, 40)
    _paint(rows, 2, 5, (240, 210, 20))
    _paint(rows, 8, 11, (40, 60, 200))
    _paint(rows, 16, 19, (210, 40, 40))
    assert colors_from_rows(rows, 2) == ["yellow", "blue"]
