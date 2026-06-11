@echo off
REM Secure Offline Industrial Knowledge Engine Launch Script for Windows

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Starting the Flask Server...
python app.py
pause
