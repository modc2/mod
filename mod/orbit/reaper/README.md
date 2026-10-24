# reaper

The fleet's scale-to-zero activator: sleeps idle modules, wakes them on first byte. Memory, idle timeout, and per-module pin/sleep/wake. App for build's REAPER.

The agent itself runs inside `orbit/build`; this module is its card and its app. The page calls build's API directly (`/api/build/...`) with the build session already in the browser, so build gates every write exactly as the AGENTS tab does — this module holds no credential.

    m reaper            # what it is
    m reaper/status     # live state from build
    m reaper/serve      # app on :51487 → /reaper/
