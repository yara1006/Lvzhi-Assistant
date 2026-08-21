#!/bin/bash
cd /home/ubuntu/backend
source /home/ubuntu/backend/.venv/bin/activate
exec /home/ubuntu/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
