"""fetch - HTTP requests and web content fetching"""
import requests
from typing import Dict, Any, Optional
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    """Strip HTML tags and extract readable text"""
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = {'script', 'style', 'noscript'}
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._skip:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag in self._skip:
            self._depth -= 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if self._depth <= 0:
            self.parts.append(data.strip())

    def get_text(self):
        import re
        text = ' '.join(self.parts)
        return re.sub(r'\n{3,}', '\n\n', re.sub(r' {2,}', ' ', text)).strip()


class Skill:
    description = "Fetch URL content or make HTTP API requests. Returns text, JSON, or parsed HTML."

    def forward(self, url: str, method: str = "GET", headers: dict = None,
                body: dict = None, json_body: dict = None,
                extract_text: bool = True, timeout: int = 15,
                max_length: int = 50000, **kwargs) -> Dict[str, Any]:
        """
        Fetch a URL or make an API request.

        Args:
            url: URL to fetch
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            headers: Request headers dict
            body: Form data body
            json_body: JSON request body
            extract_text: If True, strip HTML and return readable text
            timeout: Request timeout in seconds
            max_length: Max response length (truncates)
        """
        if not url or not url.strip():
            return {"success": False, "error": "empty url"}

        try:
            req_headers = {"User-Agent": "Mozilla/5.0 (compatible; Agent/2.0)"}
            if headers:
                req_headers.update(headers)

            r = requests.request(
                method=method.upper(), url=url,
                headers=req_headers, data=body, json=json_body,
                timeout=timeout, allow_redirects=True
            )

            content_type = r.headers.get('content-type', '')
            result = {
                "success": r.ok,
                "status": r.status_code,
                "url": r.url,
                "content_type": content_type,
            }

            # JSON response
            if 'json' in content_type:
                try:
                    result["json"] = r.json()
                    return result
                except Exception:
                    pass

            # HTML - optionally extract text
            text = r.text[:max_length]
            if extract_text and 'html' in content_type:
                parser = _TextExtractor()
                parser.feed(text)
                result["text"] = parser.get_text()[:max_length]
            else:
                result["text"] = text

            if len(r.text) > max_length:
                result["truncated"] = True
                result["total_length"] = len(r.text)

            return result
        except requests.exceptions.Timeout:
            return {"success": False, "error": f"timeout after {timeout}s", "url": url}
        except Exception as e:
            return {"success": False, "error": str(e), "url": url}

    def test(self):
        r = self.forward("https://httpbin.org/json")
        return isinstance(r, dict) and "success" in r
