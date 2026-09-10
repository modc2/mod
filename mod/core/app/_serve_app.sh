#!/bin/bash
cd /root/mod/mod/core/app
export API_URL_INTERNAL="http://localhost:8000"
npm run dev -- -p 3001
