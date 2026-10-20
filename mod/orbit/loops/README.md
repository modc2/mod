# loops

Loop agents: run a command in a module, hand its output to an agent, wait, repeat — until it passes, the agent prints LOOP_DONE, or you stop it. App for build's ⟳ LOOPS (owner-only).

The agent itself runs inside `orbit/build`; this module is its card and its app. The page calls build's API directly (`/api/build/...`) with the build session already in the browser, so build gates every write exactly as the AGENTS tab does — this module holds no credential.

    m loops            # what it is
    m loops/status     # live state from build
    m loops/serve      # app on :51481 → /loops/
