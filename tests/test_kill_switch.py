from __future__ import annotations

from trading_bot.safety.kill_switch import KillSwitch, KillSwitchState


def test_clear_allows_new_trades() -> None:
    ks = KillSwitch()
    assert ks.can_open_new_trades() is True
    ks.assert_can_open_new_trades()


def test_pause_blocks_entries_but_not_full_stop() -> None:
    ks = KillSwitch()
    ks.pause_new_trades("page invalid")
    assert ks.state is KillSwitchState.PAUSE_NEW_TRADES
    assert ks.can_open_new_trades() is False
    assert ks.should_stop_loop() is False
    ks.resume()
    assert ks.is_clear() is True


def test_stop_bot_stops_loop() -> None:
    ks = KillSwitch()
    ks.stop_bot("operator")
    assert ks.should_stop_loop() is True
    ks.resume("should_not_clear")
    assert ks.state is KillSwitchState.STOP_BOT


def test_emergency_cannot_be_resumed_accidentally() -> None:
    ks = KillSwitch()
    ks.emergency_stop("manual")
    assert ks.close_all_requested is True
    ks.resume()
    assert ks.state is KillSwitchState.EMERGENCY_STOP
    ks.pause_new_trades("downgrade")
    assert ks.state is KillSwitchState.EMERGENCY_STOP
    ks.reset_emergency("operator_reset")
    assert ks.is_clear() is True
