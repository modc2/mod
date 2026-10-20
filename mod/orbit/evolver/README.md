# evolver

The meta agent: improves your most recently updated mods one run at a time — standing timers, a task queue, a focus list and a rotation — inside a weekly budget. App for build's EVOLVER.

The agent itself runs inside `orbit/build`; this module is its card and its app. The page calls build's API directly (`/api/build/...`) with the build session already in the browser, so build gates every write exactly as the AGENTS tab does — this module holds no credential.

    m evolver            # what it is
    m evolver/status     # live state from build
    m evolver/serve      # app on :51482 → /evolver/
