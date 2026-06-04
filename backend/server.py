"""FastAPI-сервер управления кондиционерами MDV/Midea по локальной сети.

Особенности:
- Несколько устройств описываются в devices.json (см. devices.example.json).
- Все /api/* защищены токеном из переменной окружения AC_API_TOKEN
  (если переменная не задана — доступ открыт, удобно для локальной отладки).
- Если рядом лежит собранный фронт (каталог static/ или env AC_STATIC_DIR),
  он раздаётся с того же порта — отдельный веб-сервер не нужен.

Запуск (локально):  uvicorn server:app --host 0.0.0.0 --port 8000
В контейнере запуск делает CMD из Dockerfile.
"""
import asyncio
import hmac
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from msmart.device import AirConditioner as AC

BASE = Path(__file__).resolve().parent
DEVICES_FILE = Path(os.environ.get("AC_DEVICES_FILE", BASE / "devices.json"))
STATIC_DIR = Path(os.environ.get("AC_STATIC_DIR", BASE / "static"))
API_TOKEN = os.environ.get("AC_API_TOKEN")

Mode = AC.OperationalMode
Fan = AC.FanSpeed
Swing = AC.SwingMode

app = FastAPI(title="MDV AC")


# --------------------------------------------------------------------------
# Токен-аутентификация
# --------------------------------------------------------------------------
async def require_token(
    authorization: str | None = Header(None),
    x_api_token: str | None = Header(None),
):
    if not API_TOKEN:
        return
    presented = x_api_token
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:]
    if not presented or not hmac.compare_digest(presented, API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing token")


# --------------------------------------------------------------------------
# Устройства и подключения
# --------------------------------------------------------------------------
def load_devices() -> dict[str, dict]:
    if not DEVICES_FILE.exists():
        return {}
    raw = json.loads(DEVICES_FILE.read_text("utf-8"))
    return {str(d["id"]): d for d in raw}


DEVICES = load_devices()
_conns: dict[str, AC] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock(dev_id: str) -> asyncio.Lock:
    return _locks.setdefault(dev_id, asyncio.Lock())


async def get_dev(dev_id: str) -> AC:
    cfg = DEVICES.get(dev_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="unknown device")
    dev = _conns.get(dev_id)
    if dev is None:
        dev = AC(ip=cfg["ip"], port=cfg.get("port", 6444), device_id=int(cfg["id"]))
        await dev.authenticate(cfg["token"], cfg["key"])
        _conns[dev_id] = dev
    return dev


async def safe(dev_id: str, op):
    """Выполнить операцию; при обрыве связи переподключиться один раз."""
    async with _lock(dev_id):
        try:
            return await op(await get_dev(dev_id))
        except HTTPException:
            raise
        except Exception:
            _conns.pop(dev_id, None)
            return await op(await get_dev(dev_id))


def snapshot(dev: AC) -> dict:
    return {
        "online": dev.online,
        "power": dev.power_state,
        "mode": dev.operational_mode.name,
        "target": dev.target_temperature,
        "indoor": dev.indoor_temperature,
        "outdoor": dev.outdoor_temperature,
        "fan": dev.fan_speed.name,
        "turbo": dev.turbo,
        "swing": dev.swing_mode.name,
        # display (LED) надёжно не читается — управляется только тоглом
    }


class SetReq(BaseModel):
    power: bool | None = None
    mode: str | None = None      # AUTO/COOL/DRY/HEAT/FAN_ONLY
    target: float | None = None  # целевая температура
    fan: str | None = None       # AUTO/LOW/MEDIUM/HIGH/MAX/SILENT
    turbo: bool | None = None    # буст
    swing: str | None = None     # OFF/VERTICAL/HORIZONTAL/BOTH
    display: bool | None = None  # любое значение = переключить LED


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.get("/api/devices", dependencies=[Depends(require_token)])
async def list_devices():
    return [
        {"id": str(c["id"]), "name": c.get("name") or c["ip"], "ip": c["ip"]}
        for c in DEVICES.values()
    ]


@app.get("/api/devices/{dev_id}/state", dependencies=[Depends(require_token)])
async def state(dev_id: str):
    async def op(dev):
        await dev.refresh()
        return snapshot(dev)
    return await safe(dev_id, op)


@app.post("/api/devices/{dev_id}/set", dependencies=[Depends(require_token)])
async def set_state(dev_id: str, req: SetReq):
    async def op(dev):
        await dev.refresh()
        if req.power is not None:
            dev.power_state = req.power
        if req.mode is not None:
            dev.operational_mode = Mode[req.mode.upper()]
        if req.target is not None:
            dev.target_temperature = float(req.target)
        if req.fan is not None:
            dev.fan_speed = Fan[req.fan.upper()]
        if req.turbo is not None:
            dev.turbo = req.turbo
        if req.swing is not None:
            dev.swing_mode = Swing[req.swing.upper()]
        dev.beep = True
        await dev.apply()
        if req.display is not None:
            await dev.toggle_display()
        await dev.refresh()
        return snapshot(dev)
    return await safe(dev_id, op)


@app.get("/api/health")
async def health():
    return {"ok": True, "devices": len(DEVICES)}


# --------------------------------------------------------------------------
# Раздача собранного фронта (если есть)
# --------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
