# 15 Windows 打包与 GPU 加速

本文档说明 Windows 下如何用 PyInstaller 把 DuAD 上位机打成 onedir `.exe`，
以及如何让目标机**自动加载 GPU**（CUDA + TensorRT）推理。

> 与 Linux 不同：Linux 用 `setup_env.sh` 在目标机联网 `pip` 安装 GPU 依赖；
> Windows 的 onnxruntime-gpu wheel **不自带 CUDA 运行库**，TensorRT 也**没有
> pip 库包**，因此采用「可选 GPU 支持包」方案——GPU DLL 不随主程序打包，作为
> 独立 zip 解压到 exe 同目录即可自动加速；缺省时回退 CPU，程序仍可运行。

## 1. 为什么 .exe 不能自动加载 GPU

Windows 打包产物是 PyInstaller **onedir**（不是单文件）：

```text
dist\DuAD\
  DuAD.exe                 # 启动器
  _internal\               # 全部 Python / QML / 资源
    onnxruntime\capi\
      onnxruntime_providers_cuda.dll     # ✅ 已打包
      onnxruntime_providers_tensorrt.dll # ✅ 已打包
```

provider DLL 已经打进包，但它们加载时会按 `PATH` 解析真正的 CUDA / TensorRT
运行库（`cublas64_13.dll` / `cudnn64_9.dll` / `nvinfer_10.dll` 等）。这些库
**不在包里**，找不到时 onnxruntime **静默回退 CPU**（日志只有一句
`EP Error`，不会报错），所以表面上「能跑、但推理不用 GPU」。

## 2. 什么能打包、什么不能

| 组件 | 能否随包分发 | 说明 |
|---|---|---|
| NVIDIA 显卡驱动（≥585） | ❌ 不能 | 系统内核驱动，目标机单独装官方安装器（CUDA 13 要求驱动 ≥585） |
| CUDA 运行库 cuBLAS/cuDNN/cudart | ✅ 能 | 可再分发 DLL |
| TensorRT（nvinfer 等） | ✅ 能 | 可再分发（遵守 NVIDIA EULA） |

Linux 的 `.tar.gz` 里「nvidia 驱动包」其实也是 CUDA 运行库 + TensorRT，
真正的显卡驱动在哪个平台都打不进应用包。

## 3. 打包命令

```powershell
# 只打主程序（CPU-only，无 GPU 的机器也能跑）
DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0

# 打主程序 + GPU 支持包
DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0 --with-gpu
```

产物（`dist/`）：

```text
DuAD_1.0.0_Windows_x64.zip             # 主程序（CPU-only，~200MB）
DuAD_GPU_runtime_1.0.0_Windows_x64.zip # GPU 支持包（可选，--trt min 约 1.2GB）
SHA256SUMS                             # 两个 zip 的校验和
```

`--with-gpu` 在打完主程序 zip 之后调用 `collect_gpu_dlls_win.py`：

- 收集 CUDA 运行库 + TensorRT DLL → `DuAD_GPU_runtime_*.zip`；
- 同时复制 `nvidia\` + `tensorrt\` 到 `dist\DuAD\`（本地验证用）。

主程序 zip 在复制 GPU 库**之前**生成，因此保持 CPU-only、体积小。

## 4. GPU 支持包单独生成

也可以只打主程序、单独生成或更新 GPU 支持包：

```powershell
DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\collect_gpu_dlls_win.py 1.0.0
```

参数：

| 参数 | 作用 |
|---|---|
| `--trt min`（默认） | TensorRT 只收核心 DLL + `nvinfer_builder_resource_ptx_10.dll`（PTX 覆盖所有显卡架构，首次建引擎稍慢），总量约 1.2GB |
| `--trt full` | TensorRT 全量（含各 SM 架构 builder 资源），约 3.4GB，各架构建引擎最快 |
| `--into-app` | 同时复制到 `dist\DuAD\`（本地 GPU 验证） |

体积参考（未压缩）：

| 组件 | 大小 |
|---|---|
| CUDA 运行库（cuBLAS/cuDNN/cudart） | ~1.14GB |
| TensorRT 核心（含 PTX） | ~670MB |
| TensorRT 全量（含所有 sm* builder） | ~2.26GB |

## 5. 目标机部署

1. 安装 NVIDIA 显卡驱动（≥585）。
2. 解压主程序 `DuAD_<版本>_Windows_x64.zip` 得到 `DuAD\` 目录。
3. 解压 GPU 支持包，把里面的 `nvidia\` 和 `tensorrt\` 两个文件夹放到
   `DuAD.exe` **同级**目录：

```text
DuAD\
  DuAD.exe
  _internal\
  nvidia\      <- 支持包
  tensorrt\    <- 支持包
  README_GPU.txt
```

4. 双击 `DuAD.exe`，日志（`%USERPROFILE%\DuAD_app.log`）出现：

```text
模型预热完成（['TensorrtExecutionProvider', 'CUDAExecutionProvider', ...]）
```

即表示已启用 GPU；未放支持包则推理回退 CPU，程序仍可运行。

## 6. GPU DLL 加载机制（frozen 路径约定）

打包后的程序在启动时（`main.py` 与 `backend/alg/deploy/onnx_infer.py` 双保险）
把以下目录注入 `PATH`：

```text
exe 同目录\nvidia\cu13\bin\x86_64\   # cuBLAS / cudart
exe 同目录\nvidia\cudnn\bin\         # cuDNN
exe 同目录\tensorrt\bin\             # TensorRT
```

`collect_gpu_dlls_win.py` 生成的支持包结构严格对齐上述路径（pip 包原样复制），
因此**不要改动 `nvidia\` / `tensorrt\` 的内部目录结构**。

开发环境（非打包）下，这些 DLL 分别来自：

- CUDA 运行库：`pyqml_win\Lib\site-packages\nvidia\{cu13,cudnn}\`
- TensorRT：环境变量 `TENSORRT_LIB_DIR`（收集脚本会兜底读注册表），或
  `backend\libs_win_tensorrt\bin\`

## 7. 常见问题

1. **放了支持包还是走 CPU？**
   看 `%USERPROFILE%\DuAD_app.log`：出现 `EP Error` 说明某 DLL 缺失；确认
   驱动 ≥585（旧驱动 `cudaSetDevice` 报 801 回退 CPU）。

2. **`TENSORRT_LIB_DIR` 读不到？**
   它是用户级环境变量，某些启动方式（IDE / 服务 / 已运行进程 spawn 的子进程）
   不会继承；`collect_gpu_dlls_win.py` 已做注册表兜底，正常命令行运行没问题。

3. **支持包太大？**
   用默认 `--trt min`（~1.2GB）。只有需要最快建引擎时才 `--trt full`。

4. **相关脚本**
   | 脚本 | 作用 |
   |---|---|
   | `scripts/package_win.py` | Windows 主程序打包（`--with-gpu` 附带 GPU 支持包） |
   | `scripts/collect_gpu_dlls_win.py` | 单独收集/更新 GPU 支持包 |
   | `scripts/DuAD_win.spec` | PyInstaller spec（`excludes` 排除 `nvidia*`/`tensorrt*`） |
