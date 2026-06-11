# Windows Setup Guide for Industrial Knowledge & Competency Mapping Engine

This document provides step-by-step instructions for running the IIK-CME application on Windows systems.

## System Requirements
- **Windows 10 or later** (Windows 7 may work but is not officially supported)
- **Python 3.8+** (download from [python.org](https://www.python.org/downloads/))
- **Visual C++ Build Tools** (required for some Python packages on Windows)
- **At least 500 MB free disk space**

## Step 1: Install Python

1. Download Python from [python.org](https://www.python.org/downloads/)
2. **Important**: Check the box "Add Python to PATH" during installation
3. Click "Install Now" or customize as needed
4. Verify installation by opening Command Prompt and typing:
   ```cmd
   python --version
   ```

## Step 2: Clone or Extract the Project

1. Navigate to where you want to store the project
2. Either:
   - Clone from Git: `git clone <repository-url>`
   - Or extract the project ZIP file

3. Open Command Prompt and navigate to the project folder:
   ```cmd
   cd path\to\ikss
   ```

## Step 3: Create Virtual Environment

In Command Prompt (from the project folder):

```cmd
python -m venv venv
```

This creates a `venv` folder containing an isolated Python environment.

## Step 4: Activate Virtual Environment

Choose **one** of these methods:

### Option A: Command Prompt (Batch)
```cmd
venv\Scripts\activate.bat
```

### Option B: PowerShell
```powershell
.\venv\Scripts\Activate.ps1
```

**Note:** If PowerShell gives an "execution policy" error, run:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Option C: Git Bash
```bash
source venv/Scripts/activate
```

Your prompt should now show `(venv)` at the beginning, indicating the virtual environment is active.

## Step 5: Install Dependencies

With the virtual environment active:

```cmd
pip install -r requirements.txt
```

This installs all required packages:
- Flask (web framework)
- SQLAlchemy (database ORM)
- pandas (data processing)
- openpyxl (Excel file handling)
- scikit-learn (machine learning)
- networkx (graph analysis)
- numpy (numerical computing)

## Step 6: Run the Application

### Using the provided batch script (easiest):
```cmd
run.bat
```

### Using PowerShell:
```powershell
.\run.ps1
```

### Manual method:
```cmd
venv\Scripts\activate.bat
python app.py
```

You should see output similar to:
```
 * Running on http://127.0.0.1:5000
 * Press CTRL+C to quit
```

## Step 7: Access the Application

Open your web browser and navigate to:
- **Dashboard**: `http://127.0.0.1:5000`
- **Search**: `http://127.0.0.1:5000/search`
- **Admin Panel**: `http://127.0.0.1:5000/admin`

### Admin Credentials (default):
- Username: `admin`
- Password: `tata@1945`

## Troubleshooting

### Issue: `python` command not recognized
**Solution:** 
- Make sure you checked "Add Python to PATH" during Python installation
- Restart Command Prompt after installing Python
- Use `python --version` to verify

### Issue: Virtual environment won't activate
**Solution:**
- Try the batch file: `venv\Scripts\activate.bat`
- Or use PowerShell: `.\venv\Scripts\Activate.ps1`
- Make sure you're in the project directory

### Issue: "No module named 'flask'"
**Solution:**
- Ensure virtual environment is activated (you should see `(venv)` in your prompt)
- Run `pip install -r requirements.txt` again
- Try: `pip install flask sqlalchemy pandas openpyxl`

### Issue: "Address already in use" on port 5000
**Solution:**
- Another application is using port 5000
- Close the other application, or modify `app.py` to use a different port:
  - Change `app.run(host='127.0.0.1', port=5000)` to `app.run(host='127.0.0.1', port=5001)`

### Issue: Database file not found or permission denied
**Solution:**
- This is usually fixed by our Windows path normalization (in `database.py`)
- If issues persist, check that the `ikss` folder has write permissions
- Right-click folder → Properties → Security → Edit

### Issue: Cannot write to uploads folder
**Solution:**
- Right-click the `uploads` folder → Properties
- Go to Security tab → Edit
- Select your user → Check "Full Control" → Apply

## Windows-Specific Compatibility Fixes

This project includes several Windows compatibility fixes:

1. **Database Path Normalization** (`database.py`)
   - Converts Windows backslashes to forward slashes for SQLite URLs
   - Ensures compatibility with URL format

2. **Virtual Environment Scripts** (`run.bat`, `run.ps1`)
   - Batch script for Command Prompt users
   - PowerShell script for PowerShell users

3. **Updated README**
   - Platform-specific installation and running instructions

## Data Ingestion

1. Navigate to **Admin Panel** at `http://127.0.0.1:5000/admin`
2. Log in with credentials (see Step 7)
3. Upload your Excel files:
   - `complete_trim_station_data.xlsx`
   - `TCF_1.xlsx`
   - Any other supported Excel files

The system will automatically:
- Process the files
- Build the local SQLite database
- Index for search functionality
- Create knowledge graph relationships

## Stopping the Application

Press `Ctrl+C` in the Command Prompt/PowerShell window running the Flask server.

## Notes for Windows Users

- **Firewall**: Windows Firewall might prompt you to allow Python. Click "Allow" for local network access.
- **Antivirus**: Some antivirus software may slow down file processing. Add the project folder to your antivirus whitelist if needed.
- **Path length**: Windows has a 260-character path limit. If you see errors, move the project to a folder closer to the root (e.g., `C:\Projects\ikss`)

## Additional Resources

- [Python Documentation](https://docs.python.org/3/)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)

## Getting Help

If you encounter issues:
1. Check the `system.log` file in the project folder for error details
2. Review the terminal output for error messages
3. Verify all installation steps were completed correctly
4. Ensure Windows has the latest updates installed
