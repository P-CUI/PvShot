# PvShot

PvShot 是一个 Windows 下的 OpenFOAM 批量截图工具，用 ParaView 的 `pvpython` 批量导出切片图片。

项目包含两种入口：

- `run_screenshots.bat`：命令行批处理入口，会先准备环境再运行截图脚本。
- `start_gui.bat`：启动基于 `pywebview` 的桌面 GUI。

## 依赖

- Windows
- ParaView，例如 ParaView 5.10，需包含 `pvpython.exe`
- Python 3
- GUI 模式需要安装 `pywebview`

安装 GUI 依赖：

```bat
pip install -r requirements.txt
```

## 使用

命令行模式：

```bat
run_screenshots.bat
```

GUI 模式：

```bat
start_gui.bat
```

手动运行时，请使用 ParaView 自带的 `pvpython.exe`：

```bat
pvpython.exe screenshot_openfoam_slices.py
```

不要用普通 Python 直接运行 `screenshot_openfoam_slices.py`，因为它依赖 `paraview.simple` 和 `paraview.servermanager`。

## 配置

运行准备脚本后会生成或更新 `pvshot_config.json`。该文件保存 ParaView 路径、OpenFOAM case 路径、输出目录、时间步选择、色标范围和 `.pvsm` state file 等设置。

如果 OpenFOAM case 路径包含中文或其他非 ASCII 字符，准备脚本会把 case 复制到 ASCII 安全路径，例如 `D:\pvshot_work\cases\...`，以避免 ParaView OpenFOAM reader 读取失败。

## 主要文件

- `screenshot_openfoam_slices.py`：ParaView 侧截图逻辑。
- `pvshot_gui.py`：桌面 GUI 和 Python-JavaScript bridge。
- `prepare_pvshot_environment.ps1`：环境检测和 case 准备脚本。
- `pvshot_config.json`：运行配置。
- `run_screenshots.bat`：批处理截图入口。
- `start_gui.bat`：GUI 启动入口。
