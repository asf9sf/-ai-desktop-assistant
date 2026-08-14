@echo off
setlocal
cd /d "%~dp0"

set "PY=D:\jiqishijue\anaconda3\envs\pythonProject2\python.exe"

echo ======================================================
echo   Smart Assistant - Starting (conda: pythonProject2)
echo ======================================================

if not exist "%PY%" (
  echo [!] Python not found: %PY%
  pause
  exit /b 1
)

"%PY%" -c "import PyQt6, face_recognition, cv2" >nul 2>&1
if errorlevel 1 (
  echo [!] Dependencies missing, installing...
  "%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)

"%PY%" main.py
pause
