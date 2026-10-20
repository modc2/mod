# appwatch

Watchdog that keeps the build console's app port up and restarts it when it goes dark; live uptime, misses and recoveries. App for build's APPWATCH.

The agent itself runs inside `orbit/build`; this module is its card and its app. The page calls build's API directly (`/api/build/...`) with the build session already in the browser, so build gates every write exactly as the AGENTS tab does — this module holds no credential.

    m appwatch            # what it is
    m appwatch/status     # live state from build
    m appwatch/serve      # app on :51486 → /appwatch/
