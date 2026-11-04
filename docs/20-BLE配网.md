# 20 BLE 配网（只配 Wi-Fi，不动运动控制）

> 目标：在「平台控制」页的**二轴 / Z 轴两张卡片**上点「搜索板卡」，
> 通过 BLE 把 Wi-Fi 名称/密码交给板子；板子自己连路由器、把凭据存 NVS，
> 然后上位机自动改用新 IP 连原来的 TCP 通道。

## 1 边界（重要）

- **BLE 只做配网**。运动、点动、急停、轨迹仍然走 TCP；
  固件 BLE 侧只放行 `wifi / net / ver / help` 四条命令，避免绕过 TCP 的急停语义。
- 板子**一直慢速广播**（500ms 连接间隔的广播，不是保持连接）。
  配网连接只在用户操作时建立，成功后断开，广播继续。
- ESP32 只支持 **2.4GHz**；扫描结果以板子为准，不用电脑网卡列表。
- 配网前必须**停止运动**；二轴/Z 轴的桥在 `bleScan()` 里会拒绝运动中/回零中的请求。

## 2 硬件与协议栈

| 项 | 值 |
|---|---|
| 芯片 | ESP32-WROOM-32UE（BLE 4.2 + Wi-Fi） |
| BLE 栈 | NimBLE（`CONFIG_BT_NIMBLE_ENABLED=y`） |
| 广播名 | `DuAD-XY-55C6`（固定）；Z 板用固定前缀 `DuAD-Z-`（后缀按实物，不需要用户在界面选） |
| 广播 | 500~600ms 间隔，可连接；不带 128-bit UUID（31 字节装不下） |
| Service | `d0ad0001-9a1b-4c2d-8e3f-1a2b3c4d5e6f` |
| RX（主机→板） | `d0ad0002-9a1b-4c2d-8e3f-1a2b3c4d5e6f`（Write / WriteNoRsp） |
| TX（板→主机） | `d0ad0003-9a1b-4c2d-8e3f-1a2b3c4d5e6f`（Notify） |

RX/TX 是**按行文本**，和 USB/TCP 控制台完全同构；上位机按 `\n` 分帧，
按 MTU 分片写入，固件按 `\n` 重组。

## 3 配网流程

```text
1. 点面板右上角刷新 → QtBluetooth 只找固定名字（XY = DuAD-XY-55C6；Z = DuAD-Z- 前缀）
2. 扫到第一台就自动连（不再有板卡下拉/选择步骤）
3. GATT 连接成功 → 第一条必须发连接口令：
      <token>
   ← #OK auth
4. 自动读状态 + 自动扫描 Wi-Fi（用户不需要再点「扫描 Wi-Fi」）：
      wifi status
   ← {"configured":1,"connected":0,"state":"connecting","ssid":"","ip":"","rssi":0}
      wifi scan
   ← {"nets_start":8}
   ← {"net":{"ssid":"313","rssi":-20,"auth":3}}
   ← {"net":{"ssid":"110","rssi":-45,"auth":3}}
   ← ...
   ← {"nets_end":8}
5. 面板列出全部 Wi-Fi（按 RSSI 强→弱）；点某一项弹出密码框
6. 密码为空时「连接」禁用；输入后：
      wifi set {"ssid":"313","pass":"secret"}
   ← #OK
   每 1s 轮询 wifi status，最多约 30s
7. 成功：状态「已连接 Wi-Fi」，自动用新 IP 连 TCP；弹窗显示 IP 后自动关闭
8. 失败：弹窗显示原因（auth/no_ap/timeout/dhcp），板子自动回滚
```

「忘记网络」只对**板子当前保存的 SSID** 生效：清除板子 NVS 里的凭据并断开；
如果点的是别的网络，弹窗会提示“该网络不是板子当前保存的网络”。

失败时：

```text
wifi rollback     ← #OK
```

## 4 固件存储与回滚

- 当前配置仍由 `esp_wifi` 以 `WIFI_STORAGE_FLASH` 存 NVS；
- **额外一份 `wifi_bak` 备份**（namespace `wifi_bak`，key `cfg`，含 magic + `wifi_config_t`）：
  1. 改 Wi-Fi 前，把当前配置写入备份；
  2. 新配置拿到 IP 后，把新配置写入备份（成为“上次成功”）；
  3. 25s 没拿到 IP → 自动恢复备份并重连；
  4. `wifi rollback` 手动恢复；`wifi forget` 清空配置与备份。
- 掉电/重启：`net_link_wifi_autostart()` 用当前配置连；失败会按 2/4/8/15s 退避重试。
- 配网状态 `state/reason` 由 `net_link_wifi_info_t` 暴露，`wifi status` 输出 JSON。

## 5 固件模块

| 模块 | 作用 |
|---|---|
| `components/ble_prov/` | NimBLE 广播 + GATT + 口令认证 + 命令队列 + 输出改道到 BLE |
| `components/console_io/` | 新增 `console_io_get_sink()`，BLE 临时接管输出后恢复 |
| `components/net_link/` | 新增 `net_link_wifi_rollback()`、`net_link_token_matches()`、备份/状态字段 |
| `main/console_cmds.c` | `wifi status/scan/rollback`、`wifi set <json>`；`console_cmds_exec` 对 `wifi set ` 整行保留 |
| `main/app_main.c` | `net_link_start()` 之后 `ble_prov_start(console_cmds_exec, "xy"/"z")` |
| `sdkconfig.defaults` | NimBLE、BLE-only、Wi-Fi/BLE 共存；关 Wi-Fi IRAM 优化（BLE 占 IRAM） |

> `CONFIG_ESP_WIFI_IRAM_OPT=n` / `CONFIG_ESP_WIFI_RX_IRAM_OPT=n` 是**为了装下 BLE 栈**
> （二轴板实测 IRAM 溢出 1.5KB）。只影响 Wi-Fi 吞吐，不影响 1Hz/5Hz 控制。

## 6 上位机模块

| 模块 | 作用 |
|---|---|
| `backend/Src/ble_provision.py` | `QtBleLink`（QtBluetooth 扫描/GATT/分片/通知）+ `BleProvisioner`（状态机/协议/超时/回滚） |
| `backend/Src/stage_bridge.py` / `zstage_bridge.py` | BLE 属性/槽；配网前断 TCP、暂停 json 轮询；成功后改 `stage/host` 并自动连 TCP |
| `pages/minipages/BleConfigPanel.qml` | 共用配网面板（搜索/选板/扫描 Wi-Fi/SSID/密码/连接/忘记/状态） |
| `pages/minipages/StageSetupPanel.qml` / `ZStageSetupPanel.qml` | 引入面板，口令取各自「口令」输入框 |
| `scripts/gen_translations.py` | 新增文案 EN/TW |
| `tests/test_ble_provision.py` | 假 BLE 链路：成功 / 认证失败 / Wi-Fi 失败回滚 |

## 7 失败矩阵

| 场景 | 表现 | 处理 |
|---|---|---|
| 口令错 | `#ERR bad token`，BLE 断开 | UI 提示重新填口令 |
| 搜不到板 | 扫描 8s 无结果 | 检查供电/距离/是否被别的中心连着 |
| Wi-Fi 密码错 | `wifi status` state=failed reason=auth | 固件自动回滚；UI 显示“密码错误” |
| SSID 不存在/5GHz | reason=no_ap | 用板子扫描结果重选 2.4GHz |
| DHCP 超时 | reason=dhcp/timeout | 回滚；检查路由器 DHCP |
| BLE 中途断开 | 上位机提示断开 | 重新搜索；板子仍在广播 |
| TCP 正连着 | BLE 命令回 `#ERR busy` | 上位机 `bleScan()` 会先断开 TCP |
| 运动中配网 | 桥直接拒绝 | 先停止平台 |
| 旧固件无 BLE | 搜不到/无服务 | 先烧新固件 |

## 8 真机验证顺序

1. 烧录固件，USB 控制台看 `BLE 名称: DuAD-XY-xxxx`；
2. 电脑蓝牙打开；点「搜索板卡」；
3. 连上后「扫描 Wi-Fi」→ 选 2.4GHz → 输密码 →「连接并保存」；
4. 观察状态：`正在连接 Wi-Fi…` → `已连接 Wi-Fi`；
5. 确认板子 USB 控制台 `net` 显示新 IP；
6. 确认上位机卡片自动连上 TCP（`json` 遥测恢复）；
7. 错误密码测试：应回滚到原网络，UI 显示“密码错误”；
8. 断电重启：板子应自动连回上次成功的网络，BLE 广播仍在。

## 9 已知限制 / 后续

- 当前 GATT 未启用 LE Secure Connections，口令在 BLE 链路上是明文（局域网实验环境可接受）；
  量产建议开 bonding + NVS 加密。
- `wifi scan` 同步执行，BLE 任务已独立（6KB 栈），不会堵住 TCP/控制台；
- 扫描结果按行输出（`{"nets_start":N}` / 每行一个 `{"net":{...}}` / `{"nets_end":N}`），
  不再受 `console_io` 单次 512 字节限制；最多列 24 条，按 RSSI 从强到弱。
