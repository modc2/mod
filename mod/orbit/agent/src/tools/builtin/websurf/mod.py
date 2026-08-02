"""websurf - fetch and extract content from web pages"""
import requests
from typing import Dict, Any, Optional
from html.parser import HTMLParser


class TextExtractor(HTMLParser):
    """Extract readable text from HTML, stripping scripts/styles."""
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False
        self._skip_tags = {"script", "style", "noscript", "svg"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self.skip = True
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            cleaned = data.strip()
            if cleaned:
                self.text.append(cleaned)

    def get_text(self):
        return "\n".join(self.text)


class Skill:
    description = "Fetch a web page and extract its text content"

    def forward(self, url: str, max_chars: int = 10000,
                selector: str = None, **kwargs) -> Dict[str, Any]:
        """Fetch a URL and return extracted text content.

        Args:
            url: URL to fetch
            max_chars: max characters to return (default 10000)
            selector: optional - 'links' to extract links, 'title' for page title
        """
        if not url.strip():
            return {"success": False, "error": "empty url"}

        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; ModAgent/1.0)"}
            r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            r.raise_for_status()

            content_type = r.headers.get("content-type", "")

            # JSON response
            if "json" in content_type:
                return {
                    "success": True,
                    "url": url,
                    "type": "json",
                    "content": r.text[:max_chars],
                    "length": len(r.text),
                }

            # plain text
            if "text/plain" in content_type:
                return {
                    "success": True,
                    "url": url,
                    "type": "text",
                    "content": r.text[:max_chars],
                    "length": len(r.text),
                }

            # HTML - extract text
            html = r.text

            if selector == "links":
                return self._extract_links(url, html, max_chars)
            if selector == "title":
                return self._extract_title(url, html)

            extractor = TextExtractor()
            extractor.feed(html)
            text = extractor.get_text()

            return {
                "success": True,
                "url": url,
                "type": "html",
                "content": text[:max_chars],
                "length": len(text),
                "truncated": len(text) > max_chars,
            }

        except requests.exceptions.Timeout:
            return {"success": False, "url": url, "error": "request timed out"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "url": url, "error": "connection failed"}
        except Exception as e:
            return {"success": False, "url": url, "error": str(e)}

    def _extract_links(self, url: str, html: str, max_chars: int) -> Dict[str, Any]:
        """Extract all links from HTML."""
        class LinkParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []
                self.current_href = None
                self.current_text = []
            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    a = dict(attrs)
                    self.current_href = a.get("href", "")
                    self.current_text = []
            def handle_data(self, data):
                if self.current_href is not None:
                    self.current_text.append(data.strip())
            def handle_endtag(self, tag):
                if tag == "a" and self.current_href:
                    text = " ".join(self.current_text).strip()
                    if self.current_href and text:
                        self.links.append({"href": self.current_href, "text": text})
                    self.current_href = None
        p = LinkParser()
        p.feed(html)
        return {"success": True, "url": url, "type": "links", "links": p.links[:100]}

    def _extract_title(self, url: str, html: str) -> Dict[str, Any]:
        """Extract page title."""
        class TitleParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_title = False
                self.title = ""
            def handle_starttag(self, tag, attrs):
                if tag == "title": self.in_title = True
            def handle_data(self, data):
                if self.in_title: self.title += data
            def handle_endtag(self, tag):
                if tag == "title": self.in_title = False
        p = TitleParser()
        p.feed(html)
        return {"success": True, "url": url, "title": p.title.strip()}

    def test(self):
        r = self.forward("https://httpbin.org/html")
        return isinstance(r, dict) and r.get("success", False)
