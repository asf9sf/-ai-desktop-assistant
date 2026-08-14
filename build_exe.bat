@echo off
chcp 65001 >nul
REM ==========================================
REM  智能助手桌面应用 - 打包脚本 (Windows)
REM  使用 PyInstaller 生成单文件 .exe
REM ==========================================

setlocal enabledelayedexpansion
set PROJ_DIR=%~dp0
cd /d "%PROJ_DIR%"

echo [1/5] 检查并安装 PyInstaller...
python -m pip install --upgrade pip >nul
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo PyInstaller 未安装，正在安装...
    python -m pip install pyinstaller || goto :err
)

echo [2/5] 清理旧的构建文件...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist SmartAssistant.spec del /q SmartAssistant.spec

echo [3/5] 启动打包（onedir模式，比onefile更稳定，启动更快）...

REM 注意：人脸识别库文件较多，推荐 --onedir。若需onefile可改为--onefile，但启动会慢很多
REM  关键：添加隐藏导入和数据文件

pyinstaller ^
  --name SmartAssistant ^
  --noconfirm ^
  --windowed ^
  --onedir ^
  --clean ^
  --icon NONE ^
  --add-data "config;config" ^
  --add-data "face_db;face_db" ^
  --hidden-import face_recognition ^
  --hidden-import face_recognition_models ^
  --hidden-import dlib ^
  --hidden-import cv2 ^
  --hidden-import pypinyin ^
  --hidden-import psutil ^
  --hidden-import PyQt6 ^
  --hidden-import PyQt6.QtCore ^
  --hidden-import PyQt6.QtGui ^
  --hidden-import PyQt6.QtWidgets ^
  --collect-all face_recognition ^
  --collect-all face_recognition_models ^
  --collect-all pypinyin ^
  main.py || goto :err

echo [4/5] 复制必要运行时文件到输出目录...
set DIST_DIR=%PROJ_DIR%dist\SmartAssistant
if not exist "%DIST_DIR%\config" mkdir "%DIST_DIR%\config"
if not exist "%DIST_DIR%\face_db" mkdir "%DIST_DIR%\face_db"
REM 如果已通过add-data复制过，就不再覆盖
if not exist "%DIST_DIR%\config\settings.json" copy /y "%PROJ_DIR%config\settings.json" "%DIST_DIR%\config\" >nul
if not exist "%DIST_DIR%\face_db\faces.json"    copy /y "%PROJ_DIR%face_db\faces.json" "%DIST_DIR%\face_db\" >nul

echo [5/5] 完成！
echo.
echo =====================================================
echo   打包完成！可执行文件位于：
echo   %DIST_DIR%\SmartAssistant.exe
echo.
echo   使用说明：
echo   1) 首次使用：双击 SmartAssistant.exe，先注册人脸。
echo   2) 大模型：请先启动 Ollama (ollama serve) 或 LM Studio
echo      的本地服务端，或在设置中配置自定义 OpenAI 兼容接口。
echo   3) 需要打包成单个 .exe：把本脚本中 --onedir 改为 --onefile
echo      （注意：onefile 首次启动会慢 5~15 秒）
echo =====================================================
pause
exit /b 0

:err
echo.
echo ❌ 构建失败，错误代码 %errorlevel%
pause
exit /b 1
