#!/bin/bash
# Stop the core app (and its gateway).
pm2 delete app 2>/dev/null && echo "stopped app" || echo "app not running"
pm2 delete gateway 2>/dev/null && echo "stopped gateway" || echo "gateway not running"
