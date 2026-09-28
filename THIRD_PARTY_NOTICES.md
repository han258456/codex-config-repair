# 第三方组件

本项目自身代码采用 MIT 协议，见根目录 `LICENSE`。该协议不替代以下第三方组件各自的许可证。

软件使用动态加载的 Python / Qt 运行时，便携目录中的 `_internal` 需要保留。构建时会把安装包中提供的许可证文本与精确版本清单复制到 `licenses/`。

| 组件 | 项目与源码 |
| --- | --- |
| Python | https://www.python.org/ / https://github.com/python/cpython |
| PySide6、Shiboken6 | https://doc.qt.io/qtforpython-6/ / https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| Qt 6 Core / Gui / Widgets | https://www.qt.io/ / https://code.qt.io/cgit/qt/qtbase.git/ |
| TOML Kit | https://github.com/python-poetry/tomlkit |
| psutil | https://github.com/giampaolo/psutil |
| PyInstaller（打包工具） | https://pyinstaller.org/ / https://github.com/pyinstaller/pyinstaller |

第三方组件版权和许可证归各自权利人所有。PySide6 安装包元数据声明 LGPL-3.0-only / GPL 替代许可；便携包同时附有 GNU LGPLv3、GPLv3 文本及安装包提供的其他许可文本。各组件的实际许可证以其随附文件和官方源码为准。

应用代码通过源码包提供，依赖及其源代码由上表中的官方项目提供。本项目未修改 Qt/PySide6 库；使用标准 Python 导入和动态链接，不将 Qt 静态链接到应用。
