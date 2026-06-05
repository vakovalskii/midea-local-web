"""Общее ядро управления кондеями MDV/Midea по локальной сети.

Выделено из server.py, чтобы переиспользовать одну и ту же логику и в
standalone-пульте (server.py), и в multisite-агенте (agent.py).

Тут только локальная работа с устройством через msmart (TCP 6444): загрузка
devices.json, кэш подключений, чтение состояния, применение команд.
"""
import asyncio
import json
import os
from pathlib import Path

from msmart.device import AirConditioner as AC

BASE = Path(__file__).resolve().parent
DEVICES_FILE = Path(os.environ.get("AC_DEVICES_FILE", BASE / "devices.json"))

Mode = AC.OperationalMode
Fan = AC.FanSpeed
Swing = AC.SwingMode


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


async def _get_dev(dev_id: str) -> AC:
    cfg = DEVICES.get(dev_id)
    if cfg is None:
        raise KeyError(f"unknown device {dev_id}")
    dev = _conns.get(dev_id)
    if dev is None:
        dev = AC(ip=cfg["ip"], port=cfg.get("port", 6444), device_id=int(cfg["id"]))
        await dev.authenticate(cfg["token"], cfg["key"])
        _conns[dev_id] = dev
    return dev


async def _safe(dev_id: str, op):
    """Выполнить операцию; при обрыве связи переподключиться один раз."""
    async with _lock(dev_id):
        try:
            return await op(await _get_dev(dev_id))
        except KeyError:
            raise
        except Exception:
            _conns.pop(dev_id, None)
            return await op(await _get_dev(dev_id))


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


def device_list() -> list[dict]:
    return [
        {"id": str(c["id"]), "name": c.get("name") or c["ip"], "ip": c["ip"]}
        for c in DEVICES.values()
    ]


async def get_state(dev_id: str) -> dict:
    async def op(dev):
        await dev.refresh()
        return snapshot(dev)
    return await _safe(str(dev_id), op)


async def apply_set(dev_id: str, req: dict) -> dict:
    """req: dict с опциональными полями power/mode/target/fan/turbo/swing/display."""
    async def op(dev):
        await dev.refresh()
        if req.get("power") is not None:
            dev.power_state = bool(req["power"])
        if req.get("mode") is not None:
            dev.operational_mode = Mode[str(req["mode"]).upper()]
        if req.get("target") is not None:
            dev.target_temperature = float(req["target"])
        if req.get("fan") is not None:
            dev.fan_speed = Fan[str(req["fan"]).upper()]
        if req.get("turbo") is not None:
            dev.turbo = bool(req["turbo"])
        if req.get("swing") is not None:
            dev.swing_mode = Swing[str(req["swing"]).upper()]
        dev.beep = True
        await dev.apply()
        if req.get("display") is not None:
            await dev.toggle_display()
        await dev.refresh()
        return snapshot(dev)
    return await _safe(str(dev_id), op)
