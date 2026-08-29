"""MqttBridge 冒烟：登录参数/TLS/发布订阅（不访问真实网络）。"""
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QGuiApplication

PROJECT = os.environ.get(
    "DUAD_PROJECT_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT, "backend"))

import Src.mqtt_bridge as mod
from Src.mqtt_bridge import MqttBridge


class FakeClient:
    MQTT_ERR_SUCCESS = 0
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.username = None
        self.password = None
        self.tls_called = False
        self.published = []
        self.subscribed = []
        self.loop_started = False
        self.connected = True
        FakeClient.instances.append(self)

    def username_pw_set(self, u, p):
        self.username, self.password = u, p

    def tls_set(self):
        self.tls_called = True

    def connect(self, address, port, keepalive):
        self.address, self.port, self.keepalive = address, port, keepalive
        return FakeClient.MQTT_ERR_SUCCESS

    def loop_start(self):
        self.loop_started = True
        # 模拟 Broker 立即回调连接成功
        self.on_connect(self, None, None, 0)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, qos):
        self.published.append((topic, payload, qos))
        return type("Info", (), {"rc": 0})()

    def subscribe(self, topic, qos):
        self.subscribed.append((topic, qos))


class FakeMqttModule:
    CallbackAPIVersion = type("V", (), {"VERSION1": 1})()
    MQTT_ERR_SUCCESS = 0
    Client = FakeClient


app = QGuiApplication(sys.argv)
results = []


def check(name, cond):
    results.append((name, cond))
    print(("PASS" if cond else "FAIL"), "-", name)


real_mqtt = mod.mqtt
mod.mqtt = FakeMqttModule
try:
    bridge = MqttBridge()
    errors = []
    bridge.mqttError.connect(lambda m: errors.append(m))

    bridge.connectServer("", 1883, "", "", 60, False)
    check("空地址直接报错", len(errors) == 1)

    bridge.connectServer("broker.example.com", 8883,
                         "paid_user", "secret", 60, True)
    deadline = time.time() + 2
    while time.time() < deadline and not bridge.connected:
        time.sleep(0.02)
    check("模拟 Broker 连接成功", bridge.connected is True)

    client = FakeClient.instances[-1]
    check("用户名密码已设置", client.username == "paid_user"
          and client.password == "secret")
    check("TLS 已开启", client.tls_called is True)
    check("地址/端口/心跳正确", client.address == "broker.example.com"
          and client.port == 8883 and client.keepalive == 60)

    bridge.subscribe("duad/test", 0)
    check("订阅 Topic", client.subscribed and client.subscribed[-1][0] == "duad/test")

    ok = bridge.publish("duad/test", "hello", 1)
    check("发布成功", ok and client.published[-1] == ("duad/test", "hello", 1))
finally:
    mod.mqtt = real_mqtt

failed = [n for n, ok in results if not ok]
print()
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
