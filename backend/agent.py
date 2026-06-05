"""Multisite-агент: живёт в LAN кондея, сам открывает исходящее WSS в центр.

Направление соединения — ИЗ офиса/дома НАРУЖУ. Поэтому проброс портов и
публичный IP в точке с кондеем НЕ нужны (как Cloudflare Tunnel / ngrok).

Агент:
  1) знает свои локальные кондеи (devices.json в своей LAN);
  2) подключается к центру по WSS, логинится AGENT_TOKEN-ом;
  3) регистрирует список устройств;
  4) принимает команды (state/set), выполняет их локально через ac_core
     (TCP 6444 к кондею) и шлёт ответ обратно тем же сокетом.

ENV:
  CENTER_WS_URL   wss://<your-center-domain>/agent/ws  (или ws://127.0.0.1:8000/agent/ws локально)
  AGENT_ID        office | home | ...   (уникальное имя точки)
  AGENT_TOKEN     общий секрет агент<->центр
  AC_DEVICES_FILE путь к devices.json (по умолчанию рядом)
"""
import asyncio
import json
import logging
import os

import websockets

import ac_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agent")

CENTER_WS_URL = os.environ["CENTER_WS_URL"]
AGENT_ID = os.environ.get("AGENT_ID", "agent")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")


async def _handle_cmd(ws, msg: dict):
    rid = msg.get("req_id")
    dev_id = str(msg.get("device_id"))
    action = msg.get("action")
    try:
        if action == "state":
            data = await ac_core.get_state(dev_id)
        elif action == "set":
            data = await ac_core.apply_set(dev_id, msg.get("payload") or {})
        else:
            raise ValueError(f"unknown action {action}")
        await ws.send(json.dumps({"type": "resp", "req_id": rid, "ok": True, "data": data}))
    except Exception as e:  # noqa: BLE001 — любую ошибку возвращаем в центр
        log.warning("cmd %s/%s failed: %r", action, dev_id, e)
        await ws.send(json.dumps({"type": "resp", "req_id": rid, "ok": False, "error": str(e)}))


async def _session():
    headers = {"Authorization": f"Bearer {AGENT_TOKEN}"} if AGENT_TOKEN else {}
    async with websockets.connect(
        CENTER_WS_URL,
        additional_headers=headers,
        ping_interval=20,
        ping_timeout=20,
        max_size=2 ** 20,
    ) as ws:
        devices = ac_core.device_list()
        await ws.send(json.dumps({"type": "register", "agent_id": AGENT_ID, "devices": devices}))
        log.info("registered in center as '%s' with %d device(s)", AGENT_ID, len(devices))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get("type")
            if t == "cmd":
                asyncio.create_task(_handle_cmd(ws, msg))
            elif t == "ping":
                await ws.send(json.dumps({"type": "pong"}))


async def main():
    log.info("agent '%s' → %s", AGENT_ID, CENTER_WS_URL)
    backoff = 1
    while True:
        try:
            await _session()
        except Exception as e:  # noqa: BLE001
            log.warning("disconnected: %r; reconnect in %ds", e, backoff)
        else:
            log.warning("session ended; reconnect in %ds", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    asyncio.run(main())
