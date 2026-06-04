"""Добавить кондиционер MDV/Midea в devices.json ПО IP-адресу.

Запускать НА том сервере, что стоит в одной локальной сети с кондеем
(обнаружение идёт по UDP, через интернет к удалённому IP не сработает).

Скрипт:
  1) находит устройство по IP (узнаёт его id и версию протокола);
  2) логинится в публичное облако NetHome Plus и получает token/key
     (для V3-устройств; нужен исходящий интернет на сервере);
  3) проверяет обе кодировки udpid реальной LAN-аутентификацией;
  4) дописывает рабочую запись в devices.json.

Примеры:
  python provision.py --ip 192.168.1.100 --name "Дом"
  python provision.py --ip 10.0.0.50 --name "Офис" --region EU
"""
import argparse
import asyncio
import json
from pathlib import Path

from msmart.discover import Discover
from msmart.lan import Security
from msmart.cloud import NetHomePlusCloud
from msmart.device import AirConditioner as AC

DEVICES_FILE = Path(__file__).resolve().parent / "devices.json"


def load() -> list[dict]:
    if DEVICES_FILE.exists():
        return json.loads(DEVICES_FILE.read_text("utf-8"))
    return []


def save(devices: list[dict]) -> None:
    DEVICES_FILE.write_text(json.dumps(devices, indent=2, ensure_ascii=False), "utf-8")


async def get_token_candidates(dev_id: int, region: str, tries: int = 8):
    """Публичное облако флапает — пробуем несколько раз, отдаём обе кодировки."""
    last = None
    for _ in range(tries):
        try:
            cloud = NetHomePlusCloud(region)
            await cloud.login()
            out = {}
            for endian in ("big", "little"):
                udpid = Security.udpid(dev_id.to_bytes(6, endian)).hex()
                out[endian] = await cloud.get_token(udpid)
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(2)
    raise RuntimeError(f"не удалось получить token/key из облака: {last}")


async def main() -> int:
    ap = argparse.ArgumentParser(description="Добавить кондей по IP в devices.json")
    ap.add_argument("--ip", required=True, help="локальный IP кондиционера")
    ap.add_argument("--name", required=True, help="человекочитаемое имя")
    ap.add_argument("--region", default="US", help="регион облака: US / EU / ...")
    args = ap.parse_args()

    print(f"[1/4] Ищу устройство по {args.ip} …")
    dev = await Discover.discover_single(host=args.ip, auto_connect=False)
    if dev is None:
        print("  ✗ устройство не найдено (тот же сегмент сети? включён ли кондей?)")
        return 1
    print(f"  ✓ найдено: id={dev.id} version=V{dev.version} type={hex(dev.type)}")

    if dev.version < 3:
        # V1/V2 не требуют token/key
        entry = {"name": args.name, "ip": args.ip, "port": dev.port,
                 "id": dev.id, "token": "", "key": ""}
        devices = [d for d in load() if str(d["id"]) != str(dev.id)] + [entry]
        save(devices)
        print(f"  ✓ V{dev.version}: token/key не нужны. Записано в {DEVICES_FILE.name}.")
        return 0

    print("[2/4] Получаю token/key из облака NetHome Plus …")
    cands = await get_token_candidates(dev.id, args.region)

    print("[3/4] Проверяю аутентификацию по LAN …")
    chosen = None
    for endian, (token, key) in cands.items():
        probe = AC(ip=args.ip, port=dev.port, device_id=dev.id)
        try:
            await probe.authenticate(token, key)
            chosen = (token, key)
            print(f"  ✓ сработала кодировка udpid: {endian}")
            break
        except Exception:  # noqa: BLE001
            continue
    if chosen is None:
        print("  ✗ ни один token/key не прошёл LAN-аутентификацию")
        return 2

    print("[4/4] Сохраняю в devices.json …")
    entry = {"name": args.name, "ip": args.ip, "port": dev.port,
             "id": dev.id, "token": chosen[0], "key": chosen[1]}
    devices = [d for d in load() if str(d["id"]) != str(dev.id)] + [entry]
    save(devices)
    print(f"  ✓ готово. Устройств в {DEVICES_FILE.name}: {len(devices)}.")
    print("    Перезапусти контейнер/сервер, чтобы он подхватил новое устройство.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
