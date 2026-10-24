# guardian

The agent that screens every suggestion before it enters a module's queue — console switch, per-module house rules, held edits and AUTO mode. App for build's GUARDIAN.

The agent itself runs inside `orbit/build`; this module is its card and its app. The page calls build's API directly (`/api/build/...`) with the build session already in the browser, so build gates every write exactly as the AGENTS tab does — this module holds no credential.

    m guardian            # what it is
    m guardian/status     # live state from build
    m guardian/serve      # app on :51484 → /guardian/
