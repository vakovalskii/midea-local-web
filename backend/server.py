"""FastAPI standalone-пульт MDV/Midea (одна точка, без центра).

Логика управления вынесена в ac_core.py (общая с multisite-агентом).
Этот сервер сам разговаривает с кондеями в своей LAN и раздаёт UI.
Для мультисайта (несколько офисов/домов за NAT) — см. agent.py + center/.

Запуск (локально):  uvicorn server:app --host 0.0.0.0 --port 8000
"""
import hmac
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

import ac_core

STATIC_DIR = Path(os.environ.get("AC_STATIC_DIR", Path(__file__).resolve().parent / "static"))
API_TOKEN = os.environ.get("AC_API_TOKEN")

app = FastAPI(title="MDV AC")


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


@app.get("/api/devices", dependencies=[Depends(require_token)])
async def list_devices():
    return ac_core.device_list()


@app.get("/api/devices/{dev_id}/state", dependencies=[Depends(require_token)])
async def state(dev_id: str):
    try:
        return await ac_core.get_state(dev_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown device")


@app.post("/api/devices/{dev_id}/set", dependencies=[Depends(require_token)])
async def set_state(dev_id: str, req: dict):
    try:
        return await ac_core.apply_set(dev_id, req or {})
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown device")


@app.get("/api/health")
async def health():
    return {"ok": True, "devices": len(ac_core.DEVICES)}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
