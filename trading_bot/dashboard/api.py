"""Local control API. LIVE mode cannot be enabled from this UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trading_bot.monitoring.health_monitor import HealthMonitor
from trading_bot.safety.kill_switch import KillSwitch

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"


class ControlRequest(BaseModel):
    reason: str = "dashboard"


def create_app(health: HealthMonitor, kill_switch: KillSwitch) -> FastAPI:
    app = FastAPI(title="Trading Bot Dashboard", version="0.2.0")

    @app.get("/api/status")
    async def status() -> dict:
        snap = health.snapshot.as_dict()
        snap["kill_switch"] = kill_switch.state.value
        snap["kill_switch_reason"] = kill_switch.reason
        return snap

    @app.post("/api/control/start")
    async def start(body: ControlRequest) -> dict:
        return {
            "ok": False,
            "message": "Use `python main.py --run` to start the bot process. "
            "The dashboard cannot spawn a browser from a stopped process.",
            "reason": body.reason,
        }

    @app.post("/api/control/stop")
    async def stop(body: ControlRequest) -> dict:
        kill_switch.stop_bot(body.reason or "dashboard_stop")
        return {"ok": True, "state": kill_switch.state.value}

    @app.post("/api/control/pause")
    async def pause(body: ControlRequest) -> dict:
        kill_switch.pause_new_trades(body.reason or "dashboard_pause")
        return {"ok": True, "state": kill_switch.state.value}

    @app.post("/api/control/resume")
    async def resume(body: ControlRequest) -> dict:
        kill_switch.resume(body.reason or "dashboard_resume")
        return {"ok": True, "state": kill_switch.state.value}

    @app.post("/api/control/emergency")
    async def emergency(body: ControlRequest) -> dict:
        kill_switch.emergency_stop(body.reason or "dashboard_emergency")
        return {"ok": True, "state": kill_switch.state.value}

    @app.post("/api/control/close-all")
    async def close_all(body: ControlRequest) -> dict:
        kill_switch.request_close_all_bot_positions(body.reason or "dashboard_close_all")
        return {
            "ok": True,
            "state": kill_switch.state.value,
            "note": "Phase 1–2 records the request only. No positions are closed automatically.",
        }

    @app.post("/api/control/live")
    async def live() -> dict:
        raise HTTPException(
            status_code=403,
            detail="LIVE trading cannot be enabled from the dashboard.",
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    return app
