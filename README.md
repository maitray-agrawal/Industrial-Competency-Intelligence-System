# Industrial Knowledge & Competency Mapping Engine (IIK-CME)

IIK-CME is an offline-first industrial knowledge system for mapping manufacturing floor operations, tooling, skills, and syllabus content into a unified competency dashboard.

## Overview
This repository implements a secure, local application designed to run in air-gapped manufacturing environments. It ingests Excel datasets, normalizes station/process/tool/topic data, builds a local SQLite-based knowledge graph, and serves a responsive dashboard, search UI, and admin ingest interface.

## Key Features
* 100% air-gapped operation with no external API or cloud dependency
* Local SQLite database with WAL mode
* Excel ingestion for shop-floor and syllabus datasets
* Search backed by SQLite FTS5
* Heuristic mapping of stations, tools, skills, and training topics
* Competency readiness dashboard
* Interactive knowledge graph visualization using locally hosted `vis.js`
* Unified entity API contract for theory-topic reverse mapping to shop stations and tooling

## Repository Structure
```
Industrial-Knowledge-System/
├── app.py
├── database.py
├── models.py
├── data_engine.py
├── search_engine.py
├── heuristic_engine.py
├── competency_engine.py
├── graph_engine.py
├── taxonomy.py
├── logger.py
├── reingest.py
├── requirements.txt
├── README.md
├── LICENSE
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── search.html
│   └── admin.html
├── static/
│   ├── css/style.css
│   └── js/vis-network.min.js
├── screenshots/
└── uploads/
```

## Installation

### Linux / macOS
1. Open a terminal in the project folder.
2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Windows
1. Open PowerShell or Command Prompt in the project folder.
2. Create and activate a Python virtual environment:
   ```cmd
   python -m venv venv
   venv\Scripts\activate.bat
   ```
   Or in PowerShell:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```cmd
   pip install -r requirements.txt
   ```

## Running the application

### Linux / macOS
Start the Flask server:
```bash
./run.sh
```

### Windows
Use one of the following methods:

**Method 1: Batch script (Command Prompt)**
```cmd
run.bat
```

**Method 2: PowerShell script**
```powershell
.\run.ps1
```

**Method 3: Manual activation**
```cmd
venv\Scripts\activate.bat
python app.py
```

Then open a browser and visit:
* `http://127.0.0.1:5000` — Dashboard
* `http://127.0.0.1:5000/search` — Search and graph explorer
* `http://127.0.0.1:5000/admin` — Admin ingest panel

## Admin login
Use the default credentials or configure environment variables if needed:
* Username: `admin`
* Password: `tata@1945`

## Data ingestion
Use the Admin page to upload supported Excel files, including:
* `complete_trim_station_data.xlsx`
* `TCF_1.xlsx`

The system auto-detects the dataset type and builds the local index.

## Notes
* All UI assets are served locally from `static/`.
* `uploads/` is used for temporary Excel upload storage.
* Rebuild or re-ingest data from the admin panel if the dashboard data is stale.

## License
This project is licensed under the terms in `LICENSE`.
