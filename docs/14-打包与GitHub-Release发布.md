# 14 打包与 GitHub Release 发布

本文档说明如何把 DuAD 上位机打成可发布到 GitHub Release 的 Linux x64 安装包，
以及 CPU/GPU 推理依赖的自动选择策略。

## 1. 打包策略

开发机上的 `DuAD_SoftwareContent/pyqml/` 虚拟环境约 **7GB**，其中绝大部分是
GPU 动态库，不适合直接打包：

| 目录/包 | 大小 | 是否打包 |
|---|---|---|
| `pyqml/.../tensorrt_libs/` | ~4.3GB | 否 |
| `pyqml/.../nvidia/` | ~1.7GB | 否 |
| `pyqml/.../PySide6/` | ~644MB | 是（CPU 便携包） |
| `pyqml/.../onnxruntime/` | ~322MB（GPU 版） | CPU 版替代，体积小很多 |
| `backend/libs/`（大恒相机 SDK） | ~26MB | 是 |

发布产物分为两类：

1. **Installer 包**：`DuAD_<版本>_Linux_x64_Installer.tar.gz`
   - 只包含应用源码、相机 SDK、QML 资源、安装脚本，不含 Python 依赖。
   - 用户在目标机执行 `setup_env.sh`，脚本自动检测 NVIDIA 驱动并安装
     CPU 或 GPU 推理依赖（需要联网访问 PyPI）。
   - 适合无法预知目标机 Python 版本、或希望现场安装依赖的场景。

2. **CPU-Portable 包**：`DuAD_<版本>_Linux_x64_CPU-Portable.tar.zst`
   - 内置全新构建的 CPU venv（PySide6 + onnxruntime CPU）。
   - 解压后无需 pip 安装即可运行，但推理走 CPU。
   - 适合无 GPU、无网络或需要开箱即用的场景。
   - 注意：Python venv 不是完全可移植的，目标机需要与构建机有相同的
     Python 版本（CI 构建于 ubuntu-24.04，即 Python 3.12 的
     `/usr/bin/python3`）。

两种包都附带 `run.sh` 启动脚本和 `install-desktop.sh` 桌面图标安装脚本；
解压后执行一次 `bash install-desktop.sh`，即可在应用菜单中双击启动 DuAD。

两种包都附带 `SHA256SUMS` 校验和。

## 2. CPU / GPU 自动判断

### 2.1 安装阶段（setup_env.sh）

`setup_env.sh` 的 `DUAD_INSTALL_GPU` 支持三个值：

```bash
bash setup_env.sh                     # auto：自动检测（默认）
DUAD_INSTALL_GPU=1 bash setup_env.sh  # 强制安装 GPU 依赖
DUAD_INSTALL_GPU=0 bash setup_env.sh  # 强制安装 CPU 依赖
```

自动检测逻辑：

1. `/proc/driver/nvidia/version` 存在 → 认为已安装 NVIDIA 驱动。
2. 否则调用 `nvidia-smi -L`，成功 → 认为有可用 GPU。
3. 都失败 → 安装 CPU 依赖。

有驱动时安装 `requirements-gpu.txt`：
`onnxruntime-gpu` + `tensorrt_cu13_libs`（CUDA/TensorRT 运行库由 pip 提供，
不属于系统驱动）。无驱动时安装 `requirements-cpu.txt`。

### 2.2 运行阶段（onnx_infer.py）

推理时 provider 优先级固定为：

```text
TensorrtExecutionProvider → CUDAExecutionProvider → CPUExecutionProvider
```

`onnxruntime` 的 `get_available_providers()` 会自动过滤当前机器不可用的
provider，因此：

- 装了 GPU 依赖且有 NVIDIA 驱动：优先 TensorRT，其次 CUDA。
- 只装 CPU 包：只剩 CPU，自动回退。
- 检测到 NVIDIA 驱动、但当前环境只有 CPU provider 时，启动日志会输出
  一次性提示，提醒用户运行 `setup_env.sh` 升级 GPU 依赖。

## 3. 本次修改涉及的文件

| 文件 | 修改内容 |
|---|---|
| `setup_env.sh` | 增加 NVIDIA 驱动自动检测，默认 `DUAD_INSTALL_GPU=auto` |
| `DuAD_SoftwareContent/main.py` | 去掉硬编码 `lib/python3.14/site-packages`，改用 `sysconfig` 获取路径 |
| `backend/alg/deploy/onnx_infer.py` | 有驱动但无 GPU provider 时输出一次性升级提示 |
| `scripts/package.sh` | 新增：一键打包 Installer 包与 CPU-Portable 包 |
| `.github/workflows/release.yml` | 新增：推送 `v*` 标签时自动构建并发布 Release |
| `README.md` | 增加“打包与 GitHub Release”章节 |

## 4. 本地打包

### 4.1 完整打包（会创建 CPU venv，需联网）

```bash
cd DuAD_Software
bash scripts/package.sh 1.0.0
```

产物输出到 `dist/`：

```text
DuAD_1.0.0_Linux_x64_Installer.tar.gz
DuAD_1.0.0_Linux_x64_CPU-Portable.tar.zst
SHA256SUMS
```

`package.sh` 会自动排除以下内容，避免把开发环境打进包里：

- `.git/`
- `DuAD_SoftwareContent/pyqml/`（开发机 7GB venv）
- `dist/`、`__pycache__/`、`*.pyc`
- `*.onnx`、`*.ckpt`、`*.pth`、`*.pt`、`*.engine`
- `*.log`、`*.tmp`、`*.mp4`

### 4.2 快速验证（只打 Installer 包）

```bash
DUAD_SKIP_VENV=1 bash scripts/package.sh 0.1.0-test
```

该命令不会下载任何 pip 包，几十秒内可验证文件拷贝与 tar 打包是否正常。

### 4.3 校验产物

```bash
cd dist
sha256sum -c SHA256SUMS
```

## 5. 发布到 GitHub Release

### 5.1 自动发布（推荐）

仓库已配置 `.github/workflows/release.yml`。发布流程：

```bash
cd DuAD_Software

# 1. 提交本次修改
git add -A
git commit -m "build: 添加打包脚本与 GPU 驱动自动检测"

# 2. 推送到远程仓库
git push origin main

# 3. 打版本标签并推送
git tag v1.0.0
git push origin v1.0.0
```

推送 `v*` 标签后，GitHub Actions 会自动执行：

1. 在 `ubuntu-24.04` 上创建 CPU venv 并安装 `requirements-cpu.txt`。
2. 生成 Installer 包、CPU-Portable 包和 `SHA256SUMS`。
3. 用 `gh release create` 创建 Release 并上传资产。

### 5.2 手动发布

如果 Actions 不可用或需要人工控制，也可以本地打包后手动上传：

1. 执行：

   ```bash
   bash scripts/package.sh 1.0.0
   ```

2. 浏览器打开 GitHub 仓库 → Releases → Draft a new release。
3. `Choose a tag` 输入 `v1.0.0`（可同时创建标签）。
4. 填写标题和 Release Notes。
5. 上传 `dist/` 下的两个 tar 包与 `SHA256SUMS`。
6. 点击 Publish release。

## 6. 目标机安装说明

### 6.1 Installer 包

```bash
tar -xzf DuAD_1.0.0_Linux_x64_Installer.tar.gz
cd DuAD_1.0.0_Linux_x64
bash setup_env.sh                       # 自动检测驱动，安装 CPU/GPU 依赖

sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger

bash install-desktop.sh                 # 创建桌面图标（之后可在应用菜单双击启动）
python DuAD_SoftwareContent/main.py
```

### 6.2 CPU-Portable 包

```bash
tar --zstd -xf DuAD_1.0.0_Linux_x64_CPU-Portable.tar.zst
cd DuAD_1.0.0_Linux_x64

sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger

bash install-desktop.sh                 # 创建桌面图标，之后双击即可启动
./run.sh                               # 或从终端直接启动
```

> `main.py` 检测到非项目 venv 解释器时会自动 `execv` 到 `pyqml/bin/python`，
> 因此直接 `python3 DuAD_SoftwareContent/main.py` 通常也可以启动。
>
> 如果 CPU-Portable 包需要在本机使用 GPU 推理（需已装 NVIDIA 驱动）：
>
> ```bash
> DuAD_SoftwareContent/pyqml/bin/python -m pip uninstall -y onnxruntime
> DuAD_SoftwareContent/pyqml/bin/python -m pip install -r requirements-gpu.txt
> ```

## 7. 常见问题与注意事项

1. **为什么 GPU 动态库不打包？**
   TensorRT/CUDA pip 库约 6GB，且与驱动、Python 版本、GPU 架构强相关；
   GitHub Release 单文件也有 2GB 限制。由 `setup_env.sh` 在目标机按需安装
   更小、更可靠。

2. **CPU-Portable 包为什么还依赖系统 Python？**
   它打包的是 Python venv，而不是 Python 解释器本体。CI 构建于
   `ubuntu-24.04`（Python 3.12），因此目标机需要存在兼容的
   `/usr/bin/python3`。目标机 Python 版本不一致时请使用 Installer 包。

3. **目标机完全离线怎么办？**
   无 GPU：使用 CPU-Portable 包即可。
   有 GPU：需要另外制作 GPU 离线包，把 `onnxruntime-gpu`、
   `tensorrt_cu13_libs`、`nvidia-*-cu13` 的 wheel 一起分发，并可在打包前
   删除 TensorRT 中 `libnvinfer_builder_resource_win_*` 和无关 SM 架构的
   资源文件以减小体积。

4. **GitHub Actions 发布失败怎么排查？**
   打开仓库 Actions 页面查看对应 tag 的运行日志；多数问题是 pip 下载失败
   （网络波动重试即可）或 `gh` 权限不足（确认 `permissions: contents: write`
   未被改动）。

5. **Release 资产过大怎么办？**
   当前 CPU-Portable 包预计几百 MB，Installer 包约 11MB，均低于 2GB 限制。
   如果未来要发布 GPU 离线包，请使用分卷（`split`）或多文件上传，不要上传
   单个 6GB 压缩包。
