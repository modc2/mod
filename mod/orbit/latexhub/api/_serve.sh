#!/bin/bash
cd /root/mod/mod/orbit/latexhub
uvicorn api.api:app --host 0.0.0.0 --port 50200 --reload
