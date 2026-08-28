#!/bin/bash
cd "$(dirname "$0")"
uvicorn api:app --host 0.0.0.0 --port 8920 --reload
