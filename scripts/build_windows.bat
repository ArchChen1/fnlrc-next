@echo off
REM 打包 Windows 单 exe。在仓库根目录执行：
REM   scripts\build_windows.bat
REM
REM 产物：dist\fnlrc.exe
REM
REM 注意：PyInstaller 产物常被杀软误报（这是它的通病，不是本项目的问题）。
REM 分发时建议同时给 zip 版 + SHA256，条件允许就做代码签名。

setlocal
cd /d "%~dp0.."

if not exist .venv\Scripts\python.exe (
    echo [1/3] 建虚拟环境...
    uv venv .venv --python 3.12 || goto :err
)

echo [2/3] 装依赖...
REM pywebview 必须显式装上 —— 它在 requirements.txt 里是注释掉的（服务器部署不需要它），
REM 但桌面版少了它就只剩"打开浏览器"，不是原生窗口。
uv pip install --python .venv\Scripts\python.exe -r requirements.txt pyinstaller "pywebview>=5.0,<6" || goto :err

echo [3/3] 打包...
.venv\Scripts\pyinstaller.exe --clean --noconfirm scripts\fnlrc.spec || goto :err

echo.
echo 完成：dist\fnlrc.exe
echo 请在一台没装 Python 的机器上双击验证一次。
endlocal
exit /b 0

:err
echo.
echo 打包失败，退出码 %errorlevel%
endlocal
exit /b 1
