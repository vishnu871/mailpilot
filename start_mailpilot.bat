@echo off
setlocal

cd /d E:\AI-Projects\mailpilot

echo ==========================================
echo        MailPilot Background Service
echo ==========================================
echo.

echo Checking Ollama...

curl.exe -s http://127.0.0.1:11434/api/tags >nul 2>&1

if errorlevel 1 (
    echo Starting Ollama...
    start "" /min cmd /c ""%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve"
    timeout /t 5 /nobreak >nul
) else (
    echo Ollama is already running.
)

echo.
echo Starting MailPilot...
echo.

call E:\AI-Projects\mailpilot\.venv\Scripts\activate.bat

python -m uvicorn app.web.app:app --host 127.0.0.1 --port 8000

endlocal