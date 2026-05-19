# Secure Offline Industrial Knowledge Mapping Engine

A production-ready, 100% air-gapped system designed for Fortune 500 industrial manufacturing units to normalize, search, and deterministically map trades, workstations, skills, and academic theories.

## Features

- **Offline Normalization**: Ingest raw CSV data securely and locally.
- **Deterministic Heuristic Mapping**: Utilizes scikit-learn's TF-IDF vectorizer and Cosine Similarity to map Trades against Workstation requirements.
- **Sub-millisecond Search**: Leverages the SQLite FTS5 extension for instantaneous BM25-ranked full-text queries across offline documentation.
- **ACID Compliant Architecture**: SQLAlchemy ORM backing an SQLite database configured in WAL (Write-Ahead Logging) mode to ensure high concurrency and resilience against hard power-cuts.
- **Premium Air-Gapped Interface**: A sleek, dark-mode, server-side rendered (SSR) dashboard built natively with Flask, Jinja2, and custom Vanilla CSS (zero external JS frameworks or CDNs).

## Technology Stack

- **Backend**: Python, Flask, SQLAlchemy
- **Database**: SQLite3 (WAL mode, FTS5 Virtual Tables)
- **Data Engine**: Pandas, Scikit-learn (Machine Learning Heuristics)
- **Frontend**: HTML5, Vanilla CSS, Jinja2

## Quickstart Guide

1. Clone this repository to your offline industrial terminal.
2. Ensure you have Python installed.
3. Create a virtual environment and install the required standard dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install pandas scikit-learn sqlalchemy flask
   ```
4. Execute the launch script:
   ```bash
   chmod +x run.sh
   ./run.sh
   ```
5. Access the Application:
   - **Main Dashboard**: `http://127.0.0.1:5000`
   - **Admin / Ingestion Panel**: `http://127.0.0.1:5000/admin`
   - **Admin Credentials**: Username: `admin` | Password: `secure_offline_123`

## Data Ingestion Flow

Navigate to the Admin Panel to load data. Because of the strict relational constraints protecting the Golden Record, data must be ingested in the following order:

1. **Trades Data** 
2. **Workstations Data**
3. **Skills Data**
4. **Academic Theory Data**
5. **Student Induction Data**

The system automatically handles parsing, type inference, golden record normalization, re-computing the TF-IDF heuristic matrices, and rebuilding the fast search index.
