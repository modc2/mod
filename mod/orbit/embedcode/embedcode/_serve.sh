#!/bin/bash
cd /Users/broski/mod/mod/orbit/embedcode/embedcode
uvicorn api:app --host 0.0.0.0 --port 8920 --reload
