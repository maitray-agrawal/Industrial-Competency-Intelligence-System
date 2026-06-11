# Secure Offline Industrial Knowledge Engine Launch Script for Windows (PowerShell)

Write-Host "Activating virtual environment..."
& .\venv\Scripts\Activate.ps1

Write-Host "Starting the Flask Server..."
python app.py

Read-Host "Press Enter to exit"
