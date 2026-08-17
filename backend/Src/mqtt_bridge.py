"""MqttBridge — 云服务器 MQTT 通信桥。

兼容不同云端 Broker（免费/付费）：
    - 支持用户名/密码登录（付费 Broker 通常要求）
    - 支持 TLS/SSL（端口一般 8883）
    - 支持发布/订阅与日志回显

连接在后台线程执行，不阻塞 UI；回调经 Qt 信号返回。
"""
import threading
import time

from PySide6.QtCore import QObject, Slot, Signal, Property

try:
    import paho.mqtt.client as mqtt
    _HAS_PAHO = True
except Exception as _e:
    mqtt = None
    _HAS_PAHO = False
    print(f"[MqttBridge] paho-mqtt 不可用: {_e}")


class MqttBridge(QObject):
    """QML 可调用的 MQTT 桥。"""

    mqttConnected = Signal()
    mqttDisconnected = Signal()
    mqttError = Signal(str)
    messageReceived = Signal(str, str)   # (topic, payload)
    publishFinished = Signal(int)        # mid
    logMessage = Signal(str)             # 带时间戳的中文日志，供 QML 消息日志

    connectedChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client = None
        self._lock = threading.Lock()
        self._connecting = False
        self._manual_disconnect = False

    # ── 状态 ─────────────────────────────────────────────
    def _getConnected(self) -> bool:
        c = self._client
        return c is not None and c.is_connected()

    connected = Property(bool, _getConnected, notify=connectedChanged)

    def _getConnecting(self) -> bool:
        return self._connecting

    connecting = Property(bool, _getConnecting, notify=connectedChanged)

    # ── 连接 / 断开（连接在后台线程执行）──────────────────
    @Slot(str, int, str, str, int, bool)
    def connectServer(self, address: str, port: int, username: str,
                      password: str, keep_alive: int, use_tls: bool):
        if not _HAS_PAHO:
            self.mqttError.emit("paho-mqtt 未安装，无法连接云服务器")
            return
        if self.connected or self._connecting:
            self.mqttError.emit("云服务器已连接或正在连接")
            return
        address = str(address or "").strip()
        if not address:
            self.mqttError.emit("服务器地址不能为空")
            return
        self._connecting = True
        self.connectedChanged.emit()
        threading.Thread(
            target=self._connect_worker,
            args=(address, int(port), str(username or ""), str(password or ""),
                  int(keep_alive), bool(use_tls)),
            daemon=True,
        ).start()

    def _connect_worker(self, address, port, username, password, keep_alive, use_tls):
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.on_publish = self._on_publish
            client.on_subscribe = self._on_subscribe

            # 付费/私有 Broker 登录
            if username or password:
                client.username_pw_set(username, password)
            if use_tls:
                # 默认使用系统 CA；自签名证书的服务器后续可扩展 CA 路径设置
                client.tls_set()

            ret = client.connect(address, port, keep_alive)
            if ret != mqtt.MQTT_ERR_SUCCESS:
                self._connecting = False
                self.connectedChanged.emit()
                self.mqttError.emit(f"MQTT connect 返回错误码: {ret}")
                return

            self._client = client
            client.loop_start()
            self._log(f"正在连接 {address}:{port}"
                      + (" (TLS)" if use_tls else "")
                      + (f" 用户:{username}" if username else ""))
        except Exception as e:
            self._connecting = False
            self.connectedChanged.emit()
            self.mqttError.emit(f"连接云服务器失败: {e}")

    @Slot()
    def disconnectServer(self):
        client = self._client
        self._client = None
        self._connecting = False
        self._manual_disconnect = True
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception as e:
                print(f"[MqttBridge] 断开异常: {e}")
        self._manual_disconnect = False
        self.connectedChanged.emit()
        self.mqttDisconnected.emit()
        self._log("已断开云服务器")

    # ── 发布 / 订阅 ───────────────────────────────────────
    @Slot(str, str, int, result=bool)
    def publish(self, topic: str, payload: str, qos: int) -> bool:
        client = self._client
        if client is None or not client.is_connected():
            self.mqttError.emit("MQTT 未连接，无法发布消息")
            return False
        try:
            info = client.publish(topic, payload, qos=int(qos))
            self._log(f"发布 → [{topic}] {payload}")
            return info.rc == mqtt.MQTT_ERR_SUCCESS if hasattr(info, 'rc') else True
        except Exception as e:
            self.mqttError.emit(f"发布失败: {e}")
            return False

    @Slot(str, int, result=bool)
    def subscribe(self, topic: str, qos: int) -> bool:
        client = self._client
        if client is None or not client.is_connected():
            self.mqttError.emit("MQTT 未连接，无法订阅")
            return False
        try:
            client.subscribe(topic, qos=int(qos))
            return True
        except Exception as e:
            self.mqttError.emit(f"订阅失败: {e}")
            return False

    # ── paho 回调（loop 线程）─────────────────────────────
    def _on_connect(self, client, userdata, flags, rc):
        self._connecting = False
        self.connectedChanged.emit()
        if rc == 0:
            self._log("云服务器连接成功")
            self.mqttConnected.emit()
        else:
            self._client = None
            self._log(f"云服务器拒绝连接，返回码: {rc}")
            self.mqttError.emit(f"MQTT 连接失败，返回码: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        if self._manual_disconnect:
            return
        if self._client is client:
            self._client = None
        self.connectedChanged.emit()
        self.mqttDisconnected.emit()
        self._log(f"云服务器连接已断开 (rc={rc})")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="ignore")
        except Exception:
            payload = str(msg.payload)
        self._log(f"收到 ← [{msg.topic}] {payload}")
        self.messageReceived.emit(msg.topic, payload)

    def _on_publish(self, client, userdata, mid):
        self.publishFinished.emit(mid)

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        self._log(f"订阅成功 (mid={mid}, qos={granted_qos})")

    def _log(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.logMessage.emit(f"[{ts}] {text}")
