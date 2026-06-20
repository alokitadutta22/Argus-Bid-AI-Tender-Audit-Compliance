@echo off
echo ==================================================
echo Installing Dependencies...
echo ==================================================
pip install -r requirements.txt
echo.
echo ==================================================
echo Starting Sentinel Tender Audit Platform...
echo ==================================================
streamlit run tender_audit_platform.py
pause
