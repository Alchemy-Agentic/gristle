@echo off
cd /d d:\projects\gristle
.venv\Scripts\python.exe -m pytest tests\ -v --tb=short > scripts\test_output.txt 2>&1
