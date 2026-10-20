# autosnap

Snapshots every changed module into the versions ledger a few per tick; see what was snapped, what failed, and snapshot one now. App for build's AUTOSNAP.

The agent itself runs inside `orbit/build`; this module is its card and its app. The page calls build's API directly (`/api/build/...`) with the build session already in the browser, so build gates every write exactly as the AGENTS tab does — this module holds no credential.

    m autosnap            # what it is
    m autosnap/status     # live state from build
    m autosnap/serve      # app on :51485 → /autosnap/
