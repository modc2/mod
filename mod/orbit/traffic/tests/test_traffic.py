import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collector as C  # noqa: E402

SITE = """    @build_app path /build /build/*
    @jev_api path /jev/api /jev/api/*
    @jev_app path /jev /jev/*
    @zuma_alias path /zuma /zuma/*
    handle @zuma_alias {
        route {
            uri strip_prefix /zuma
            redir * /ztensor{uri} 308
    @ztensor_app path /ztensor /ztensor/*
"""
CHROME = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/151.0 Safari/537.36"


def line(uri, ts, ip="1.2.3.4", ua=CHROME, cf=True):
    h = {"User-Agent": [ua]}
    if cf:
        h["Cf-Connecting-Ip"] = [ip]
    return json.dumps({"ts": ts, "request": {"remote_ip": "172.70.1.1" if cf else ip,
                                             "uri": uri, "headers": h}})


def ingest(tmp_path, lines):
    site = tmp_path / "site.caddy"
    site.write_text(SITE)
    log = tmp_path / "access.log"
    log.write_text("\n".join(lines) + "\n")
    s = C.Store(str(tmp_path / "db"))
    c = C.Collector(s, log=str(log), site=str(site))
    while c.step():
        pass
    return s, c, log


def test_routes_and_app_of():
    import tempfile
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(SITE)
    apps, aliases = C.load_routes(f.name)
    assert {"build", "jev", "ztensor"} <= apps and aliases == {"zuma": "ztensor"}
    assert C.app_of("/jev/api/ask?x=1", apps, aliases) == "jev"
    assert C.app_of("/api/jev/ask", apps, aliases) == "jev"
    assert C.app_of("/zuma/", apps, aliases) == "ztensor"
    assert C.app_of("/", apps, aliases) == "build"
    assert C.app_of("/wp-login.php", apps, aliases) is None


def test_counts_people_not_requests(tmp_path):
    now = 1_800_000_000
    s, _, _ = ingest(tmp_path, [
        line("/jev/", now), line("/jev/api/x", now + 1), line("/jev/", now + 2, ip="5.6.7.8"),
        line("/build/", now, ua="Googlebot/2.1"),            # bot
        line("/build/", now, ip="127.0.0.1", cf=False),      # the box itself
        "not json",
    ])
    lv = C.live(s, window=600, now=now + 30)
    assert lv["total"] == 2
    assert lv["apps"] == [{"app": "jev", "users": 2, "hits": 3}]


def test_series_buckets_and_rotation(tmp_path):
    now = 1_800_000_000
    s, c, log = ingest(tmp_path, [line("/jev/", now - 3000), line("/build/", now - 60, ip="9.9.9.9")])
    out = C.series(s, "1h", now=now)
    assert len(out["buckets"]) == 60 and {x["app"] for x in out["series"]} == {"jev", "build"}
    assert sum(out["total"]) == 2
    # rotation: a new file at the same path is read from the top
    os.remove(log)
    log.write_text(line("/ztensor/", now) + "\n")
    while c.step():
        pass
    assert any(r["app"] == "ztensor" for r in C.live(s, now=now + 10)["apps"])
