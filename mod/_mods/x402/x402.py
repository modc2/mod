"""
x402_middleware.py
------------------
Minimal self-contained HTTP middleware implementing the x402 Payment Required protocol
for Solana-compatible IPFS or API services.

Usage:
    from x402_middleware import X402Middleware
"""

import json
import requests
from http import HTTPStatus
from urllib.parse import urlparse

class X402Middleware:
    """
    X402 Middleware for IPFS-like HTTP endpoints.

    - Intercepts protected paths.
    - Responds with HTTP 402 if no payment header present.
    - Verifies `X-PAYMENT` header via an x402 facilitator or custom endpoint.
    """

    def __init__(self, app, receiver, facilitator_url="https://x402.org/facilitator",
                 protected_paths=None, network="solana", price="10", currency="USDC-SPL"):
        self.app = app
        self.receiver = receiver
        self.facilitator_url = facilitator_url
        self.protected_paths = protected_paths or ["/ipfs/premium"]
        self.network = network
        self.price = price
        self.currency = currency

    def handle(self, handler):
        """Main entry — intercept HTTP handler request."""
        path = urlparse(handler.path).path

        # Skip if not protected
        if not any(path.startswith(p) for p in self.protected_paths):
            return self.app(handler)

        # Check if payment header provided
        payment = handler.headers.get("X-PAYMENT")
        if not payment:
            return self._require_payment(handler)

        # Verify payment with facilitator
        if not self._verify(payment):
            return self._require_payment(handler, invalid=True)

        # Payment valid, pass request through
        return self.app(handler)

    def _require_payment(self, handler, invalid=False):
        """Send 402 response with payment requirements."""
        body = {
            "error": "Payment Invalid" if invalid else "Payment Required",
            "paymentRequirements": [{
                "receiver": self.receiver,
                "network": self.network,
                "price": self.price,
                "currency": self.currency
            }]
        }
        data = json.dumps(body).encode()
        handler.send_response(HTTPStatus.PAYMENT_REQUIRED)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _verify(self, payment_header):
        """Verify payment with facilitator (HTTP POST)."""
        try:
            res = requests.post(
                f"{self.facilitator_url}/verify",
                json={"payment": payment_header},
                timeout=3,
            )
            return res.ok and res.json().get("valid", False)
        except Exception as e:
            print(f"[x402] verify error: {e}")
            return False

    def example(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler

        class BaseHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/ipfs/premium"):
                    self._send_json({"data": "premium IPFS object"})
                else:
                    self._send_json({"data": "public IPFS object"})

            def _send_json(self, data):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class WrappedHandler(BaseHandler):
            def __init__(self, *args, **kwargs):
                self.middleware = X402Middleware(
                    app=lambda h: BaseHandler.do_GET(h),
                    receiver="YourSolanaAddressHere",
                    protected_paths=["/ipfs/premium"]
                )
                super().__init__(*args, **kwargs)

            def do_GET(self):
                self.middleware.handle(self)

        HTTPServer(("0.0.0.0", 50149), WrappedHandler).serve_forever()
