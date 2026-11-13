#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BLE 配网状态机（不接蓝牙硬件）。

用 FakeBleLink 回放板子的行协议（2026-11 起 BLE 免口令）：
    net -> 文本行（含“口令 xxxx”）+ #OK
    wifi status -> JSON + #OK
    wifi scan -> JSON + #OK
    wifi set ... -> #OK，然后轮询 wifi status

验四件事：成功配网并读回 TCP 口令、net 输出没有口令时仍能继续、
net 被拒时的报错、Wi-Fi 失败回滚。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from PySide6.QtCore import QCoreApplication, QTimer          # noqa: E402

from Src.ble_provision import BleProvisioner, FakeBleLink    # noqa: E402

OK = "#OK"
NET_LINE = "net: 命令通道监听中，端口 3333，口令 07b63950"


def _status(**kw):
    return json.dumps({
        "configured": 1, "connected": 0, "state": "connecting",
        "ssid": "", "ip": "", "rssi": 0, "reason": "", **kw,
    }, ensure_ascii=False)


def test_success():
    link = FakeBleLink(script={
        "net": [NET_LINE, OK],
        "wifi scan": [
            '{"nets_start":2}',
            '{"net":{"ssid":"Lab-2.4G","rssi":-45,"auth":3}}',
            '{"net":{"ssid":"Lab-5G","rssi":-70,"auth":3}}',
            '{"nets_end":2}', OK,
        ],
        "wifi set": [OK],
        "wifi status": [_status(connected=1, state="connected",
                                ssid="Lab-2.4G", ip="10.0.0.7", rssi=-58), OK],
    })
    p = BleProvisioner("xy", link)
    got = []
    tokens = []
    p.provisioned.connect(lambda ip, ssid: got.append((ip, ssid)))
    p.tokenLearned.connect(tokens.append)

    p.scan()
    assert p.state == "reading", p.state
    assert not p.busy, "已连接板卡（reading）时 busy 必须为 False，否则“连接并保存”按钮会被禁用"
    assert [n["ssid"] for n in p.nets] == []      # 还没扫描
    assert link.sent[0] == "net", link.sent       # 免口令：第一条是 net
    assert tokens == ["07b63950"], tokens         # TCP 口令自动读回
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


def test_auto_scan_after_net():
    link = FakeBleLink(script={
        "net": [NET_LINE, OK],
        "wifi status": [_status(connected=1, state="connected",
                                ssid="Lab", ip="10.0.0.9"), OK],
        "wifi scan": [
            '{"nets_start":1}',
            '{"net":{"ssid":"313","rssi":-20,"auth":3}}',
            '{"nets_end":1}', OK,
        ],
    })
    p = BleProvisioner("xy", link)
    p.scan()
    app = QCoreApplication.instance()
    QTimer.singleShot(500, app.quit)
    app.exec()
    assert [n["ssid"] for n in p.nets] == ["313"], p.nets


def test_net_without_token_still_works():
    """老/异常固件的 net 输出里没有“口令”：配网照常继续，只是不发 tokenLearned。"""
    link = FakeBleLink(script={
        "net": ["net: 命令通道监听中，端口 3333", OK],
        "wifi status": [_status(), OK],
        "wifi scan": [OK],
    })
    p = BleProvisioner("z", link)
    tokens = []
    p.tokenLearned.connect(tokens.append)
    p.scan()
    assert p.state == "reading", p.state
    assert tokens == [], tokens


def test_net_rejected():
    link = FakeBleLink(script={"net": ["#ERR busy (disconnect TCP first)"]})
    p = BleProvisioner("z", link)
    p.scan()
    assert p.state == "failed", p.state
    assert "失败" in p.error, p.error


def test_wifi_failure_rolls_back():
    link = FakeBleLink(script={
        "net": [NET_LINE, OK],
        "wifi set": [OK],
        "wifi status": [_status(state="failed", reason="auth"), OK],
        "wifi rollback": [OK],
    })
    p = BleProvisioner("xy", link)
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
    test_auto_scan_after_net()
    test_net_without_token_still_works()
    test_net_rejected()
    test_wifi_failure_rolls_back()
    print("BLE 配网状态机测试全部通过（成功+读回口令 / 无口令继续 / net 被拒 / Wi-Fi 失败回滚）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
