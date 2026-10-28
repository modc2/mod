# autoruns

Drive any mod's idea queue for N steps with nobody at the wheel: one agent proposes, a second implements. Start, watch and stop runs. App for build's AUTO RUNS.

The agent itself runs inside `orbit/build`; this module is its card and its app. The page calls build's API directly (`/api/build/...`) with the build session already in the browser, so build gates every write exactly as the AGENTS tab does — this module holds no credential.

    m autoruns            # what it is
    m autoruns/status     # live state from build
    m autoruns/serve      # app on :51483 → /autoruns/
