#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BLE 配网状态机（不接蓝牙硬件）。

用 FakeBleLink 回放板子的行协议：
    <token> -> #OK auth
    wifi status -> JSON + #OK
    wifi scan -> JSON + #OK
    wifi set ... -> #OK，然后轮询 wifi status

验三件事：成功配网、认证失败、Wi-Fi 失败回滚。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from PySide6.QtCore import QCoreApplication, QTimer          # noqa: E402

from Src.ble_provision import BleProvisioner, FakeBleLink    # noqa: E402

OK = "#OK"


def _status(**kw):
    return json.dumps({
        "configured": 1, "connected": 0, "state": "connecting",
        "ssid": "", "ip": "", "rssi": 0, "reason": "", **kw,
    }, ensure_ascii=False)


def test_success():
    link = FakeBleLink(script={
        "tok": [OK + " auth"],
        "wifi scan": [json.dumps({"nets": [
            {"ssid": "Lab-2.4G", "rssi": -45, "auth": 3},
            {"ssid": "Lab-5G", "rssi": -70, "auth": 3},
        ]}), OK],
        "wifi set": [OK],
        "wifi status": [_status(connected=1, state="connected",
                                ssid="Lab-2.4G", ip="10.0.0.7", rssi=-58), OK],
    })
    p = BleProvisioner("xy", link)
    got = []
    p.provisioned.connect(lambda ip, ssid: got.append((ip, ssid)))

    p.set_token("tok")
    p.scan()
    assert p.state == "reading", p.state
    assert [n["ssid"] for n in p.nets] == []      # 还没扫描
    p.scan_wifi()
    assert [n["ssid"] for n in p.nets] == ["Lab-2.4G", "Lab-5G"]

    p.apply_wifi("Lab-2.4G", "secret")
    assert p.state == "provisioning"

    # 等 800ms 后的第一次 wifi status 轮询
    app = QCoreApplication.instance()
    QTimer.singleShot(1200, app.quit)
    app.exec()

    assert p.state == "connected", p.state
    assert got == [("10.0.0.7", "Lab-2.4G")], got
    assert any(x.startswith("wifi set ") for x in link.sent)
    payload = [x for x in link.sent if x.startswith("wifi set ")][0][9:]
    assert json.loads(payload)["ssid"] == "Lab-2.4G"


def test_bad_auth():
    link = FakeBleLink(script={"bad": ["#ERR bad token"]})
    p = BleProvisioner("z", link)
    p.set_token("bad")
    p.scan()
    assert p.state == "failed", p.state
    assert "失败" in p.error, p.error


def test_wifi_failure_rolls_back():
    link = FakeBleLink(script={
        "tok": [OK + " auth"],
        "wifi set": [OK],
        "wifi status": [_status(state="failed", reason="auth"), OK],
        "wifi rollback": [OK],
    })
    p = BleProvisioner("xy", link)
    p.set_token("tok")
    p.scan()
    p.apply_wifi("New", "bad")

    app = QCoreApplication.instance()
    QTimer.singleShot(1200, app.quit)
    app.exec()

    assert p.state == "failed", p.state
    assert "密码" in p.error, p.error
    assert "wifi rollback" in link.sent, link.sent


def main():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    test_success()
    test_bad_auth()
    test_wifi_failure_rolls_back()
    print("BLE 配网状态机测试全部通过（成功 / 认证失败 / Wi-Fi 失败回滚）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
