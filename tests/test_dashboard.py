from __future__ import annotations

from fastapi.testclient import TestClient

from trading_bot.dashboard.api import create_app
from trading_bot.monitoring.health_monitor import HealthMonitor
from trading_bot.safety.kill_switch import KillSwitch, KillSwitchState


def test_dashboard_status_and_controls() -> None:
    health = HealthMonitor()
    health.update(bot_status="running", trading_mode="DEMO", configured_symbol="BTCUSD")
    ks = KillSwitch()
    client = TestClient(create_app(health, ks))

    payload = client.get("/api/status").json()
    assert payload["bot_status"] == "running"
    assert payload["trading_mode"] == "DEMO"

    denied = client.post("/api/control/live")
    assert denied.status_code == 403

    client.post("/api/control/pause", json={"reason": "test"})
    assert ks.state is KillSwitchState.PAUSE_NEW_TRADES
    client.post("/api/control/resume", json={"reason": "test"})
    assert ks.state is KillSwitchState.CLEAR
    client.post("/api/control/emergency", json={"reason": "test"})
    assert ks.state is KillSwitchState.EMERGENCY_STOP
