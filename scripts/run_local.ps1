$ErrorActionPreference = 'Stop'
if (-not (Test-Path '.venv')) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Start-Process -FilePath '.\.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.api.main:app','--reload' -WindowStyle Hidden
& .\.venv\Scripts\python.exe -m streamlit run dashboard/streamlit_app.py
