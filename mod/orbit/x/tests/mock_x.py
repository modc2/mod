"""A stand-in for api.x.com, serving realistic v2 payloads.

The read views of the app can only be exercised with an X bearer token, which
CI will never have. `X_BASE_URL` redirects the backend's upstream, so pointing
it here drives the whole rendering path — author joins out of `includes.users`,
`t.co` expansion from `entities`, metrics, badges — with no credentials.

    python3 tests/mock_x.py &
    HOME=/tmp/xhome PORT=51997 X_BASE_URL=http://127.0.0.1:51999 \
      X_BEARER_TOKEN=mock X_API_KEY=k X_API_SECRET=s \
      X_ACCESS_TOKEN=at X_ACCESS_SECRET=as \
      x-rs/target/release/x-api

Then open http://localhost:51997/. Nothing here reaches the real X.
"""
import base64, hashlib, hmac, json, os, re, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote, unquote

# The dummy credentials the backend under test is started with. The mock is a
# strict upstream: reads must carry this bearer, and anything acting as the
# account must carry an OAuth 1.0a header whose HMAC-SHA1 signature it
# recomputes independently — so a signing or wire-encoding bug fails loudly
# here instead of as a 401 from the real X.
BEARER = "mock"
CONSUMER_KEY, CONSUMER_SECRET = "k", "s"
ACCESS_TOKEN, ACCESS_SECRET = "at", "as"
PORT = int(os.environ.get("MOCK_X_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 51999))
LOG = []  # every request seen, for GET /_mock/log

USERS = {
    "jack": {"id": "12", "name": "jack", "username": "jack",
             "description": "block head. bitcoin, nostr, #startsmall.",
             "created_at": "2006-03-21T20:50:14.000Z", "verified": True,
             "location": "everywhere", "url": "https://cash.app",
             "profile_image_url": "https://pbs.twimg.com/profile_images/1115644092329758721/AFjOr-K8_normal.jpg",
             "public_metrics": {"followers_count": 6500000, "following_count": 4900,
                                "tweet_count": 30100, "listed_count": 29000}},
    "modprotocol": {"id": "999", "name": "mod protocol", "username": "modprotocol",
             "description": "small composable modules. every module is an API, an app and an MCP server.",
             "created_at": "2024-01-02T00:00:00.000Z", "verified": False,
             "profile_image_url": "",
             "public_metrics": {"followers_count": 1240, "following_count": 88,
                                "tweet_count": 412, "listed_count": 9}},
}
TWEETS = [
    {"id": "1900000000000000001", "author_id": "12", "created_at": "2026-08-29T14:02:11.000Z",
     "lang": "en", "text": "the model context protocol is just posix pipes with a schema, and that is a compliment https://t.co/abc123",
     "public_metrics": {"retweet_count": 812, "reply_count": 240, "like_count": 9100,
                        "quote_count": 61, "impression_count": 1240000},
     "entities": {"urls": [{"url": "https://t.co/abc123", "expanded_url": "https://modelcontextprotocol.io",
                            "display_url": "modelcontextprotocol.io"}]}},
    {"id": "1900000000000000002", "author_id": "999", "created_at": "2026-08-29T09:40:00.000Z",
     "lang": "en", "text": "shipped: every MCP tool in @modprotocol now has a REST route too. one tool layer, three transports. #mcp #rust",
     "public_metrics": {"retweet_count": 14, "reply_count": 3, "like_count": 121,
                        "quote_count": 1, "impression_count": 8800},
     "referenced_tweets": [{"type": "replied_to", "id": "1899999999999999999"}]},
    {"id": "1900000000000000003", "author_id": "12", "created_at": "2026-08-27T11:15:00.000Z",
     "lang": "en", "text": "reposting because the second paragraph is the whole argument",
     "public_metrics": {"retweet_count": 3400, "reply_count": 88, "like_count": 21000,
                        "quote_count": 190, "impression_count": 3100000},
     "referenced_tweets": [{"type": "retweeted", "id": "1888888888888888888"}]},
]
INCLUDES = {"users": list(USERS.values())}
COUNTS = [{"start": f"2026-08-{d:02d}T00:00:00.000Z", "end": f"2026-08-{d+1:02d}T00:00:00.000Z",
           "tweet_count": c} for d, c in zip(range(22, 29), [412, 903, 1180, 760, 2400, 1890, 1330])]

def pct(v):
    return quote(v, safe="-._~")


def oauth_ok(method, url, query, header):
    """RFC 5849 §3.4 base string from what actually arrived on the wire."""
    if not header.startswith("OAuth "):
        return False
    oauth = {}
    for part in header[6:].split(","):
        k, _, v = part.strip().partition("=")
        oauth[k] = unquote(v.strip('"'))
    sig = oauth.pop("oauth_signature", "")
    if oauth.get("oauth_consumer_key") != CONSUMER_KEY or oauth.get("oauth_token") != ACCESS_TOKEN:
        return False
    params = sorted([(pct(k), pct(v)) for k, v in oauth.items()] +
                    [(pct(k), pct(v)) for k, v in query])
    base = "&".join([method, pct(url), pct("&".join(f"{k}={v}" for k, v in params))])
    key = f"{pct(CONSUMER_SECRET)}&{pct(ACCESS_SECRET)}".encode()
    want = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(want, sig)


# GETs that read as the account; every POST/DELETE acts as it.
USER_CONTEXT_GETS = re.compile(r"^/2/users/(me|\d+/mentions|\d+/timelines/reverse_chronological)$")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def authorized(self, body=None):
        """Strict upstream auth. Returns False (and has replied 401) on failure."""
        path, _, qs = self.path.partition("?")
        # Strict RFC 3986: a literal `+` stays a `+`, exactly like X's verifier.
        query = [(unquote(k), unquote(v)) for k, _, v in
                 (p.partition("=") for p in qs.split("&") if p)]
        auth = self.headers.get("authorization", "")
        user_ctx = self.command != "GET" or bool(USER_CONTEXT_GETS.match(path))
        if user_ctx:
            ok = oauth_ok(self.command, f"http://127.0.0.1:{PORT}{path}", query, auth)
        else:
            ok = auth == f"Bearer {BEARER}" or oauth_ok(
                self.command, f"http://127.0.0.1:{PORT}{path}", query, auth)
        LOG.append({"method": self.command, "path": path, "query": dict(query),
                    "auth": auth.split(" ")[0], "ok": ok, "body": body})
        if not ok:
            self.send({"title": "Unauthorized", "detail": "mock: bad or missing credentials"}, 401)
        return ok

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/_mock/log":
            return self.send(LOG)
        if not self.authorized():
            return
        if p == "/2/tweets/search/recent":
            return self.send({"data": TWEETS, "includes": INCLUDES,
                              "meta": {"result_count": len(TWEETS)}})
        if p == "/2/tweets/counts/recent":
            return self.send({"data": COUNTS, "meta": {"total_tweet_count": sum(c["tweet_count"] for c in COUNTS)}})
        if p == "/2/users/me":
            return self.send({"data": USERS["modprotocol"]})
        m = re.match(r"^/2/users/by/username/(.+)$", p)
        if m:
            u = USERS.get(m.group(1))
            return self.send({"data": u} if u else {"detail": "Could not find user"}, 200 if u else 404)
        m = re.match(r"^/2/users/(\d+)/(tweets|mentions)$", p)
        if m:
            rows = [t for t in TWEETS if t["author_id"] == m.group(1)] or TWEETS
            return self.send({"data": rows, "includes": INCLUDES, "meta": {"result_count": len(rows)}})
        if re.match(r"^/2/users/\d+/timelines/reverse_chronological$", p) or re.match(r"^/2/lists/\d+/tweets$", p):
            return self.send({"data": TWEETS, "includes": INCLUDES, "meta": {"result_count": len(TWEETS)}})
        m = re.match(r"^/2/users/(\d+)/(followers|following)$", p)
        if m:
            return self.send({"data": list(USERS.values()), "meta": {"result_count": len(USERS)}})
        m = re.match(r"^/2/tweets/(\d+)$", p)
        if m:
            t = next((t for t in TWEETS if t["id"] == m.group(1)), TWEETS[0])
            return self.send({"data": t, "includes": INCLUDES})
        self.send({"detail": "mock: no route " + p}, 404)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if not self.authorized(body):
            return
        p = self.path.split("?")[0]
        if p == "/2/tweets":
            return self.send({"data": {"id": "1900000000000000042", "text": body.get("text", "")}}, 201)
        m = re.match(r"^/2/users/(\d+)/(likes|retweets|following)$", p)
        if m:
            key = {"likes": "liked", "retweets": "retweeted", "following": "following"}[m.group(2)]
            return self.send({"data": {key: True}})
        self.send({"detail": "mock: no route " + p}, 404)

    def do_DELETE(self):
        if not self.authorized():
            return
        if re.match(r"^/2/tweets/\d+$", self.path):
            return self.send({"data": {"deleted": True}})
        self.send({"detail": "mock: no route " + self.path}, 404)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
