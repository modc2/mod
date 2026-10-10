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
import json, re
from http.server import BaseHTTPRequestHandler, HTTPServer

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

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
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
        if self.path.startswith("/2/tweets"):
            return self.send({"data": {"id": "1900000000000000042", "text": body.get("text", "")}})
        self.send({"data": {"ok": True}})

HTTPServer(("127.0.0.1", 51999), H).serve_forever()
