"""Центр multisite — публичная точка (TLS, домен).

Сам в LAN никуда не лезет. Принимает исходящие WSS-подключения агентов
(каждый агент сидит в своей LAN рядом с кондеями), держит реестр
«какой агент за какие устройства отвечает», раздаёт браузерам React-UI и
REST, а команды маршрутизирует нужному агенту по его сокету (RPC по req_id).

  [браузер] --HTTPS/Bearer--> ЦЕНТР --(WSS, по сокету агента)--> [агент] --TCP6444--> кондей

ENV:
  AC_API_TOKEN   токен для браузера (/api/*). Пусто = без авторизации.
  AGENT_TOKEN    общий секрет агент<->центр (проверяется на /agent/ws).
  AC_STATIC_DIR  каталог собранного фронта (по умолчанию /app/static).
  RPC_TIMEOUT    таймаут ожидания ответа агента, сек (по умолчанию 25).
"""
import asyncio
import hmac
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (Depends, FastAPI, Header, HTTPException, WebSocket,
                     WebSocketDisconnect)
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("center")

API_TOKEN = os.environ.get("AC_API_TOKEN")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
STATIC_DIR = Path(os.environ.get("AC_STATIC_DIR", "/app/static"))
RPC_TIMEOUT = float(os.environ.get("RPC_TIMEOUT", "25"))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "5"))

# реестр в памяти: agent_id -> {"ws": WebSocket, "devices": [{id,name,ip}]}
AGENTS: dict[str, dict] = {}
# ожидающие RPC: req_id -> Future
PENDING: dict[str, asyncio.Future] = {}
# подключённые браузеры (для realtime-пуша состояния)
BROWSERS: set[WebSocket] = set()
# последнее известное состояние устройств: dev_id -> snapshot
STATE_CACHE: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(_app: "FastAPI"):
    poller = asyncio.create_task(_state_poller())
    try:
        yield
    finally:
        poller.cancel()


app = FastAPI(title="MDV AC Center", lifespan=lifespan)


# --------------------------------------------------------------------------
# Браузерная авторизация
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


def _device_list() -> list[dict]:
    out = []
    for aid, a in AGENTS.items():
        for d in a["devices"]:
            out.append({
                "id": str(d["id"]),
                "name": d.get("name") or str(d["id"]),
                "ip": d.get("ip"),
                "agent": aid,
            })
    return out


def _devices_payload() -> dict:
    return {"type": "devices", "devices": _device_list()}


async def _broadcast(obj: dict) -> None:
    data = json.dumps(obj)
    for ws in list(BROWSERS):
        try:
            await ws.send_text(data)
        except Exception:  # noqa: BLE001
            BROWSERS.discard(ws)


async def _state_poller() -> None:
    """Периодически опрашивает устройства и пушит снапшоты браузерам.

    Опрашиваем только когда есть подключённые браузеры — иначе простаиваем.
    """
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        if not BROWSERS:
            continue
        for d in _device_list():
            did = d["id"]
            try:
                st = await _rpc(did, "state")
            except Exception:  # noqa: BLE001 — устройство/агент офлайн, пропускаем
                continue
            STATE_CACHE[did] = st
            await _broadcast({"type": "state", "id": did, "state": st})


def _agent_for(dev_id: str) -> str | None:
    for aid, a in AGENTS.items():
        for d in a["devices"]:
            if str(d["id"]) == str(dev_id):
                return aid
    return None


async def _rpc(dev_id: str, action: str, payload: dict | None = None) -> dict:
    aid = _agent_for(dev_id)
    if aid is None:
        raise HTTPException(status_code=404, detail="device offline (no agent connected)")
    rid = uuid.uuid4().hex
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    PENDING[rid] = fut
    try:
        await AGENTS[aid]["ws"].send_text(json.dumps({
            "type": "cmd", "req_id": rid, "device_id": str(dev_id),
            "action": action, "payload": payload,
        }))
        resp = await asyncio.wait_for(fut, RPC_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"agent '{aid}' timeout")
    finally:
        PENDING.pop(rid, None)
    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail=resp.get("error", "agent error"))
    return resp["data"]


# --------------------------------------------------------------------------
# WSS-канал агентов
# --------------------------------------------------------------------------
@app.websocket("/agent/ws")
async def agent_ws(ws: WebSocket):
    auth = ws.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else None
    if AGENT_TOKEN and (not tok or not hmac.compare_digest(tok, AGENT_TOKEN)):
        await ws.close(code=4401)
        return
    await ws.accept()
    aid = None
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t == "register":
                aid = str(msg.get("agent_id") or "agent")
                AGENTS[aid] = {"ws": ws, "devices": msg.get("devices", [])}
                log.info("agent '%s' registered: %d device(s)", aid, len(AGENTS[aid]["devices"]))
                await _broadcast(_devices_payload())
            elif t == "resp":
                fut = PENDING.get(msg.get("req_id"))
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            elif t == "pong":
                pass
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("agent '%s' ws error: %r", aid, e)
    finally:
        if aid and AGENTS.get(aid, {}).get("ws") is ws:
            AGENTS.pop(aid, None)
            log.info("agent '%s' disconnected", aid)
            await _broadcast(_devices_payload())


# --------------------------------------------------------------------------
# Браузерный REST (тот же формат, что у standalone — фронт не меняется)
# --------------------------------------------------------------------------
@app.get("/api/devices", dependencies=[Depends(require_token)])
async def list_devices():
    return _device_list()


@app.get("/api/devices/{dev_id}/state", dependencies=[Depends(require_token)])
async def state(dev_id: str):
    return await _rpc(dev_id, "state")


@app.post("/api/devices/{dev_id}/set", dependencies=[Depends(require_token)])
async def set_state(dev_id: str, req: dict):
    return await _rpc(dev_id, "set", req or {})


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "agents": list(AGENTS.keys()),
        "devices": sum(len(a["devices"]) for a in AGENTS.values()),
        "browsers": len(BROWSERS),
    }


# --------------------------------------------------------------------------
# Браузерный WSS: realtime-пуш списка устройств и их состояния
# Токен передаётся в query (?token=...), т.к. браузер не шлёт заголовки на WS.
# --------------------------------------------------------------------------
@app.websocket("/api/ws")
async def browser_ws(ws: WebSocket):
    token = ws.query_params.get("token")
    if API_TOKEN and (not token or not hmac.compare_digest(token, API_TOKEN)):
        await ws.close(code=4401)
        return
    await ws.accept()
    BROWSERS.add(ws)
    try:
        # начальный снапшот: список устройств + последние известные состояния
        await ws.send_text(json.dumps(_devices_payload()))
        for did, st in list(STATE_CACHE.items()):
            await ws.send_text(json.dumps({"type": "state", "id": did, "state": st}))
        while True:
            await ws.receive_text()  # держим канал; входящие игнорируем
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("browser ws error: %r", e)
    finally:
        BROWSERS.discard(ws)


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
