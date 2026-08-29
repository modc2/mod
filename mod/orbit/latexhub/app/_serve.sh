#!/bin/bash
cd /root/mod/mod/orbit/latexhub/app
# serve the production build; `next dev` has no build artifacts under pm2 and
# recompiles on every restart
exec npx next start -p 3200
