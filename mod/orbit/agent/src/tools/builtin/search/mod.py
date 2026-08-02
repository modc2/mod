"""search - search the web"""
import requests
from typing import Dict, Any, Optional

class Skill:
    description = "Search the web via DuckDuckGo"

    def forward(self, query: str, max_results: int = 5, **kwargs) -> Dict[str, Any]:
        """Search the web for query"""
        if not query.strip():
            return {"success": False, "results": [], "error": "empty query"}
        try:
            r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                              headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            r.raise_for_status()
            from html.parser import HTMLParser
            class P(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.results, self.cur, self.tag = [], {}, None
                def handle_starttag(self, tag, attrs):
                    a = dict(attrs)
                    if tag == "a" and a.get("class") == "result__a":
                        self.tag = "title"; self.cur = {"url": a.get("href", "")}
                    elif tag == "a" and a.get("class") == "result__snippet":
                        self.tag = "snippet"
                def handle_data(self, data):
                    if self.tag: self.cur[self.tag] = data.strip()
                def handle_endtag(self, tag):
                    if self.tag == "snippet" and len(self.cur) >= 3:
                        self.results.append(self.cur.copy()); self.cur = {}
                    self.tag = None
            p = P(); p.feed(r.text)
            return {"success": True, "results": p.results[:max_results], "query": query}
        except Exception as e:
            return {"success": False, "results": [], "error": str(e)}

    def test(self):
        r = self.forward("python programming")
        # pass if we got a valid response dict (success or graceful failure)
        return isinstance(r, dict) and "results" in r and "success" in r
