#!/bin/bash
export PORT=51442
export MALLOC_ARENA_MAX=2
exec /root/mod/mod/orbit/dydx/src/api/target/release/dydx-api
