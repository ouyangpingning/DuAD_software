#!/usr/bin/env bash
# ============================================================
# 提升 Linux 内核 USB 缓冲内存上限（usbfs_memory_mb）
#
# 大恒官方 LX FAQ「USB3 Vision 相机开采失败」的官方解法（等价 SetUSBStack.sh）：
#   U3VTL 在 /sys/module/usbcore/parameters/usbfs_memory_mb 过小（内核默认 16MB）
#   时，无法为 2448×2048 等大负载分配缓冲环 → ACQUISITION_START 返回 -1010
#   "TL Error: Unable to start acquisition"。
#
# 用法：sudo bash scripts/set_usbfs.sh
#   （无需 root 时直接运行会打印提示；写值仅对本机本次开机有效，
#     重启后失效，可配合开机脚本/GRUB 参数 usbcore.usbfs_memory_mb=1000 持久化）
# ============================================================
set -euo pipefail

SYSFS=/sys/module/usbcore/parameters/usbfs_memory_mb
TARGET="${1:-1000}"

if [ ! -f "$SYSFS" ]; then
    echo "[usbfs] 系统无 $SYSFS（非 usbcore 模块或内核不支持），无需设置。"
    exit 0
fi

CUR="$(cat "$SYSFS" 2>/dev/null || echo 0)"
if [ "$CUR" -ge "$TARGET" ]; then
    echo "[usbfs] 当前 usbfs_memory_mb = $CUR MB（已 ≥ $TARGET MB），无需修改。"
    exit 0
fi

if echo "$TARGET" > "$SYSFS" 2>/dev/null; then
    echo "[usbfs] 已将 usbfs_memory_mb 从 $CUR 提升到 $TARGET MB（本次开机有效）。"
    echo "[usbfs] 持久化建议：GRUB 内核参数加 usbcore.usbfs_memory_mb=$TARGET，"
    echo "       或把本命令写入开机脚本（重启后失效需重新执行）。"
else
    echo "[usbfs] 无法写入 $SYSFS（需要 root）。请执行："
    echo "  sudo sh -c 'echo $TARGET > $SYSFS'"
    echo "  或在 /etc/default/grub 的 GRUB_CMDLINE_LINUX 中加 usbcore.usbfs_memory_mb=$TARGET 后 update-grub"
    exit 1
fi