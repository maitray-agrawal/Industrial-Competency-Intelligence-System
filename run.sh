#!/bin/bash
# Secure Offline Industrial Knowledge Engine Launch Script

echo "Activating virtual environment..."
source venv/bin/activate

echo "Starting the Flask Server..."
python3 app.py
