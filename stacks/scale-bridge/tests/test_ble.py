"""BLE glue with bleak faked out: discovery filter, busy/cooldown, connect -> session -> disconnect."""
import asyncio
from types import SimpleNamespace

import bleak_retry_connector

from conftest import FakeClient
from scale_bridge import ble
from scale_bridge import protocol as p
from scale_bridge.config import from_env
from scale_bridge.session import Timeouts

ADDR = "AA:BB:CC:DD:EE:01"


def test_discovery_by_name_service_and_optional_address():
    s = from_env({})
    assert ble.is_scale(s, ADDR, "Body Pod 2", [])
    assert ble.is_scale(s, ADDR, "body pod 2", [])
    assert not ble.is_scale(s, ADDR, "Other Scale", [p.SVC_UUID])      # a named device must match by name
    assert ble.is_scale(s, ADDR, None, [p.SVC_UUID.upper()])            # nameless passive advert: FFF0
    assert not ble.is_scale(s, ADDR, None, [])
    pinned = from_env({"SCALE_ADDRESS": "aa:bb:cc:dd:ee:02"})
    assert not ble.is_scale(pinned, ADDR, "Body Pod 2", [])
    assert ble.is_scale(pinned, "AA:BB:CC:DD:EE:02", "Body Pod 2", [])


def test_mask_keeps_only_two_octets():
    assert ble.mask(ADDR) == "..:EE:01" and ble.mask("junk") == "?"


def test_watcher_queues_one_and_respects_busy():
    async def go():
        w = ble.Watcher(from_env({}))
        dev = SimpleNamespace(address=ADDR, name="Body Pod 2")
        adv = SimpleNamespace(local_name="Body Pod 2", service_uuids=[p.SVC_UUID], rssi=-70)
        w.on_advert(dev, adv)
        w.on_advert(dev, adv)                    # queue already full: dropped
        assert w.queue.qsize() == 1
        await w.queue.get()
        w.busy = True
        w.on_advert(dev, adv)
        assert w.queue.empty()
        w.busy, w.quiet_until = False, float("inf")
        w.on_advert(dev, adv)
        assert w.queue.empty()
    asyncio.run(go())


def test_connect_and_run_with_fake_connector(monkeypatch, frames):
    made = []

    async def fake_establish(cls, device, name, disconnected_callback=None, max_attempts=4, **kw):
        assert name == "Body Pod 2" and max_attempts == 3
        c = FakeClient(frames)
        made.append(c)
        return c

    monkeypatch.setattr(bleak_retry_connector, "establish_connection", fake_establish)
    s = from_env({})
    s = type(s)(**{**s.__dict__, "timeouts": Timeouts(session=5, impedance_wait=0.3, df_grace=0.2)})
    result, info = asyncio.run(ble.connect_and_run(s, SimpleNamespace(address=ADDR), p.profile_frame(1, 1, 170, 30)))
    assert info["result"] == "reading" and result.reading.weight_cg == 7000
    assert made[0].disconnected


def test_connect_failure_is_reported_not_raised(monkeypatch):
    async def boom(*a, **kw):
        raise bleak_retry_connector.BleakNotFoundError("gone")

    monkeypatch.setattr(bleak_retry_connector, "establish_connection", boom)
    result, info = asyncio.run(ble.connect_and_run(from_env({}), SimpleNamespace(address=ADDR), b""))
    assert result is None and info["result"] == "connect_failed" and info["error"] == "BleakNotFoundError"


def test_relay_mode_off_by_default():
    assert asyncio.run(ble.start_relays(())) is None
