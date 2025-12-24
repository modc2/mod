"""
x402_middleware.py
------------------
Comprehensive HTTP middleware implementing the x402 Payment Required protocol
for Solana-compatible IPFS or API services with enhanced features.

Features:
    - Multiple payment network support (Solana, Ethereum, Base)
    - Configurable payment verification
    - Rate limiting and caching
    - Webhook notifications
    - Detailed logging and metrics
    - CORS support
    - Multiple currency support

Usage:
    from x402_middleware import X402Middleware
    
    middleware = X402Middleware(
        app=your_app,
        receiver="YourWalletAddress",
        protected_paths=["/api/premium", "/ipfs/premium"],
        network="solana",
        price="10",
        currency="USDC"
    )
"""

import json
import time
import hashlib
import logging
import requests
from http import HTTPStatus
from urllib.parse import urlparse, parse_qs
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from functools import wraps
from threading import Lock
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("x402")


@dataclass
class PaymentRequirement:
    """Represents a payment requirement for protected resources."""
    receiver: str
    network: str = "solana"
    price: str = "10"
    currency: str = "USDC"
    description: str = ""
    expires_in: int = 3600  # seconds
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "receiver": self.receiver,
            "network": self.network,
            "price": self.price,
            "currency": self.currency,
            "description": self.description,
            "expiresIn": self.expires_in
        }


@dataclass
class PaymentVerification:
    """Result of payment verification."""
    valid: bool
    transaction_id: Optional[str] = None
    amount: Optional[str] = None
    currency: Optional[str] = None
    timestamp: Optional[int] = None
    error: Optional[str] = None


class PaymentCache:
    """Thread-safe cache for verified payments."""
    
    def __init__(self, ttl: int = 300):
        self._cache: Dict[str, tuple] = {}
        self._lock = Lock()
        self.ttl = ttl
    
    def _hash_payment(self, payment: str) -> str:
        return hashlib.sha256(payment.encode()).hexdigest()
    
    def get(self, payment: str) -> Optional[PaymentVerification]:
        key = self._hash_payment(payment)
        with self._lock:
            if key in self._cache:
                result, timestamp = self._cache[key]
                if time.time() - timestamp < self.ttl:
                    return result
                del self._cache[key]
        return None
    
    def set(self, payment: str, result: PaymentVerification):
        key = self._hash_payment(payment)
        with self._lock:
            self._cache[key] = (result, time.time())
    
    def clear_expired(self):
        now = time.time()
        with self._lock:
            expired = [k for k, (_, ts) in self._cache.items() if now - ts >= self.ttl]
            for k in expired:
                del self._cache[k]


class RateLimiter:
    """Simple rate limiter for payment verification requests."""
    
    def __init__(self, max_requests: int = 100, window: int = 60):
        self.max_requests = max_requests
        self.window = window
        self._requests: Dict[str, List[float]] = defaultdict(list)
        self._lock = Lock()
    
    def is_allowed(self, client_id: str) -> bool:
        now = time.time()
        with self._lock:
            # Clean old requests
            self._requests[client_id] = [
                ts for ts in self._requests[client_id]
                if now - ts < self.window
            ]
            if len(self._requests[client_id]) >= self.max_requests:
                return False
            self._requests[client_id].append(now)
            return True


class X402Middleware:
    """
    X402 Middleware for HTTP endpoints with payment gating.

    Supports:
        - Multiple blockchain networks (Solana, Ethereum, Base, Polygon)
        - Multiple currencies (USDC, USDT, SOL, ETH)
        - Payment caching for performance
        - Rate limiting
        - Webhook notifications
        - CORS headers
        - Detailed metrics
    """

    SUPPORTED_NETWORKS = ["solana", "ethereum", "base", "polygon", "arbitrum"]
    SUPPORTED_CURRENCIES = ["USDC", "USDT", "SOL", "ETH", "MATIC"]

    def __init__(
        self,
        app: Callable,
        receiver: str,
        facilitator_url: str = "https://x402.org/facilitator",
        protected_paths: Optional[List[str]] = None,
        network: str = "solana",
        price: str = "10",
        currency: str = "USDC",
        description: str = "Access to premium content",
        cache_ttl: int = 300,
        rate_limit: int = 100,
        rate_window: int = 60,
        webhook_url: Optional[str] = None,
        cors_origins: Optional[List[str]] = None,
        verify_timeout: int = 5,
        custom_verifier: Optional[Callable] = None,
        on_payment_success: Optional[Callable] = None,
        on_payment_failure: Optional[Callable] = None,
    ):
        self.app = app
        self.receiver = receiver
        self.facilitator_url = facilitator_url.rstrip("/")
        self.protected_paths = protected_paths or ["/ipfs/premium", "/api/premium"]
        self.network = network
        self.price = price
        self.currency = currency
        self.description = description
        self.verify_timeout = verify_timeout
        self.webhook_url = webhook_url
        self.cors_origins = cors_origins or ["*"]
        self.custom_verifier = custom_verifier
        self.on_payment_success = on_payment_success
        self.on_payment_failure = on_payment_failure
        
        # Initialize components
        self._cache = PaymentCache(ttl=cache_ttl)
        self._rate_limiter = RateLimiter(max_requests=rate_limit, window=rate_window)
        
        # Metrics
        self._metrics = {
            "total_requests": 0,
            "protected_requests": 0,
            "successful_payments": 0,
            "failed_payments": 0,
            "cached_verifications": 0,
            "rate_limited": 0,
        }
        self._metrics_lock = Lock()
        
        # Validate configuration
        self._validate_config()
        
        logger.info(f"X402 Middleware initialized for {receiver} on {network}")

    def _validate_config(self):
        """Validate middleware configuration."""
        if not self.receiver:
            raise ValueError("Receiver address is required")
        if self.network not in self.SUPPORTED_NETWORKS:
            logger.warning(f"Network '{self.network}' not in supported list: {self.SUPPORTED_NETWORKS}")
        if self.currency not in self.SUPPORTED_CURRENCIES:
            logger.warning(f"Currency '{self.currency}' not in supported list: {self.SUPPORTED_CURRENCIES}")

    def _increment_metric(self, metric: str, value: int = 1):
        """Thread-safe metric increment."""
        with self._metrics_lock:
            self._metrics[metric] = self._metrics.get(metric, 0) + value

    def get_metrics(self) -> Dict[str, int]:
        """Get current metrics."""
        with self._metrics_lock:
            return dict(self._metrics)

    def handle(self, handler) -> Any:
        """Main entry point — intercept and process HTTP requests."""
        self._increment_metric("total_requests")
        
        # Handle CORS preflight
        if hasattr(handler, 'command') and handler.command == 'OPTIONS':
            return self._send_cors_preflight(handler)
        
        path = urlparse(handler.path).path
        
        # Check if path is protected
        if not self._is_protected(path):
            return self.app(handler)
        
        self._increment_metric("protected_requests")
        
        # Get client identifier for rate limiting
        client_id = self._get_client_id(handler)
        
        # Check rate limit
        if not self._rate_limiter.is_allowed(client_id):
            self._increment_metric("rate_limited")
            return self._send_rate_limited(handler)
        
        # Check for payment header
        payment = self._get_payment_header(handler)
        if not payment:
            return self._require_payment(handler)
        
        # Check cache first
        cached = self._cache.get(payment)
        if cached:
            self._increment_metric("cached_verifications")
            if cached.valid:
                self._increment_metric("successful_payments")
                return self.app(handler)
            else:
                self._increment_metric("failed_payments")
                return self._require_payment(handler, invalid=True, error=cached.error)
        
        # Verify payment
        verification = self._verify(payment)
        self._cache.set(payment, verification)
        
        if not verification.valid:
            self._increment_metric("failed_payments")
            self._trigger_callback(self.on_payment_failure, handler, verification)
            return self._require_payment(handler, invalid=True, error=verification.error)
        
        # Payment valid
        self._increment_metric("successful_payments")
        self._trigger_callback(self.on_payment_success, handler, verification)
        self._send_webhook(handler, verification)
        
        return self.app(handler)

    def _is_protected(self, path: str) -> bool:
        """Check if path requires payment."""
        return any(path.startswith(p) for p in self.protected_paths)

    def _get_client_id(self, handler) -> str:
        """Extract client identifier from request."""
        # Try X-Forwarded-For first, then client_address
        forwarded = handler.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if hasattr(handler, 'client_address'):
            return handler.client_address[0]
        return "unknown"

    def _get_payment_header(self, handler) -> Optional[str]:
        """Extract payment header from request."""
        # Support multiple header formats
        for header in ["X-PAYMENT", "X-Payment", "Authorization"]:
            value = handler.headers.get(header)
            if value:
                # Handle Bearer token format
                if value.startswith("Bearer "):
                    return value[7:]
                return value
        
        # Also check query params
        query = parse_qs(urlparse(handler.path).query)
        if "payment" in query:
            return query["payment"][0]
        
        return None

    def _require_payment(self, handler, invalid: bool = False, error: Optional[str] = None):
        """Send 402 Payment Required response."""
        requirement = PaymentRequirement(
            receiver=self.receiver,
            network=self.network,
            price=self.price,
            currency=self.currency,
            description=self.description
        )
        
        body = {
            "error": error or ("Payment Invalid" if invalid else "Payment Required"),
            "code": "PAYMENT_INVALID" if invalid else "PAYMENT_REQUIRED",
            "paymentRequirements": [requirement.to_dict()],
            "facilitator": self.facilitator_url,
            "timestamp": int(time.time())
        }
        
        data = json.dumps(body, indent=2).encode()
        handler.send_response(HTTPStatus.PAYMENT_REQUIRED)
        self._send_cors_headers(handler)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("X-Payment-Network", self.network)
        handler.send_header("X-Payment-Currency", self.currency)
        handler.send_header("X-Payment-Amount", self.price)
        handler.end_headers()
        handler.wfile.write(data)

    def _send_cors_headers(self, handler):
        """Add CORS headers to response."""
        origin = handler.headers.get("Origin", "*")
        if "*" in self.cors_origins or origin in self.cors_origins:
            handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Access-Control-Allow-Headers", "X-PAYMENT, X-Payment, Authorization, Content-Type")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Expose-Headers", "X-Payment-Network, X-Payment-Currency, X-Payment-Amount")

    def _send_cors_preflight(self, handler):
        """Handle CORS preflight request."""
        handler.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers(handler)
        handler.end_headers()

    def _send_rate_limited(self, handler):
        """Send 429 Too Many Requests response."""
        body = json.dumps({
            "error": "Too Many Requests",
            "code": "RATE_LIMITED",
            "retryAfter": self._rate_limiter.window
        }).encode()
        
        handler.send_response(HTTPStatus.TOO_MANY_REQUESTS)
        self._send_cors_headers(handler)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Retry-After", str(self._rate_limiter.window))
        handler.end_headers()
        handler.wfile.write(body)

    def _verify(self, payment_header: str) -> PaymentVerification:
        """Verify payment with facilitator or custom verifier."""
        # Use custom verifier if provided
        if self.custom_verifier:
            try:
                result = self.custom_verifier(payment_header)
                if isinstance(result, PaymentVerification):
                    return result
                return PaymentVerification(valid=bool(result))
            except Exception as e:
                logger.error(f"Custom verifier error: {e}")
                return PaymentVerification(valid=False, error=str(e))
        
        # Default: verify with facilitator
        try:
            res = requests.post(
                f"{self.facilitator_url}/verify",
                json={
                    "payment": payment_header,
                    "receiver": self.receiver,
                    "network": self.network,
                    "expectedAmount": self.price,
                    "currency": self.currency
                },
                timeout=self.verify_timeout,
                headers={"Content-Type": "application/json"}
            )
            
            if res.ok:
                data = res.json()
                return PaymentVerification(
                    valid=data.get("valid", False),
                    transaction_id=data.get("transactionId"),
                    amount=data.get("amount"),
                    currency=data.get("currency"),
                    timestamp=data.get("timestamp")
                )
            else:
                error_msg = res.json().get("error", "Verification failed") if res.text else "Verification failed"
                return PaymentVerification(valid=False, error=error_msg)
                
        except requests.Timeout:
            logger.error("Payment verification timeout")
            return PaymentVerification(valid=False, error="Verification timeout")
        except requests.RequestException as e:
            logger.error(f"Payment verification error: {e}")
            return PaymentVerification(valid=False, error=str(e))
        except Exception as e:
            logger.error(f"Unexpected verification error: {e}")
            return PaymentVerification(valid=False, error="Internal verification error")

    def _trigger_callback(self, callback: Optional[Callable], handler, verification: PaymentVerification):
        """Safely trigger callback function."""
        if callback:
            try:
                callback(handler, verification)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _send_webhook(self, handler, verification: PaymentVerification):
        """Send webhook notification for successful payment."""
        if not self.webhook_url:
            return
        
        try:
            requests.post(
                self.webhook_url,
                json={
                    "event": "payment_success",
                    "path": handler.path,
                    "transaction_id": verification.transaction_id,
                    "amount": verification.amount,
                    "currency": verification.currency,
                    "timestamp": int(time.time())
                },
                timeout=3
            )
        except Exception as e:
            logger.warning(f"Webhook notification failed: {e}")

    def add_protected_path(self, path: str):
        """Add a new protected path."""
        if path not in self.protected_paths:
            self.protected_paths.append(path)
            logger.info(f"Added protected path: {path}")

    def remove_protected_path(self, path: str):
        """Remove a protected path."""
        if path in self.protected_paths:
            self.protected_paths.remove(path)
            logger.info(f"Removed protected path: {path}")

    def update_price(self, price: str, currency: Optional[str] = None):
        """Update payment price and optionally currency."""
        self.price = price
        if currency:
            self.currency = currency
        logger.info(f"Updated price to {price} {self.currency}")

    @staticmethod
    def create_wsgi_middleware(app, **kwargs):
        """Create WSGI-compatible middleware wrapper."""
        middleware = X402Middleware(app=lambda h: None, **kwargs)
        
        def wsgi_app(environ, start_response):
            # Create a simple handler-like object from WSGI environ
            class WSGIHandler:
                def __init__(self, env):
                    self.path = env.get('PATH_INFO', '/')
                    self.headers = {k[5:].replace('_', '-'): v 
                                   for k, v in env.items() if k.startswith('HTTP_')}
                    self.client_address = (env.get('REMOTE_ADDR', 'unknown'), 0)
            
            handler = WSGIHandler(environ)
            payment = middleware._get_payment_header(handler)
            
            if middleware._is_protected(handler.path):
                if not payment:
                    # Return 402
                    requirement = PaymentRequirement(
                        receiver=middleware.receiver,
                        network=middleware.network,
                        price=middleware.price,
                        currency=middleware.currency
                    )
                    body = json.dumps({
                        "error": "Payment Required",
                        "paymentRequirements": [requirement.to_dict()]
                    })
                    start_response('402 Payment Required', [
                        ('Content-Type', 'application/json'),
                        ('Content-Length', str(len(body)))
                    ])
                    return [body.encode()]
                
                verification = middleware._verify(payment)
                if not verification.valid:
                    body = json.dumps({"error": "Payment Invalid"})
                    start_response('402 Payment Required', [
                        ('Content-Type', 'application/json')
                    ])
                    return [body.encode()]
            
            return app(environ, start_response)
        
        return wsgi_app

    def example(self):
        """Run example server demonstrating the middleware."""
        from http.server import HTTPServer, BaseHTTPRequestHandler

        middleware = self

        class BaseHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/ipfs/premium"):
                    self._send_json({"data": "Premium IPFS content", "tier": "premium"})
                elif self.path.startswith("/api/premium"):
                    self._send_json({"data": "Premium API data", "tier": "premium"})
                elif self.path == "/metrics":
                    self._send_json(middleware.get_metrics())
                elif self.path == "/health":
                    self._send_json({"status": "healthy", "timestamp": int(time.time())})
                else:
                    self._send_json({"data": "Public content", "tier": "free"})

            def _send_json(self, data):
                body = json.dumps(data, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            
            def log_message(self, format, *args):
                logger.info(f"{self.client_address[0]} - {format % args}")

        class WrappedHandler(BaseHandler):
            def do_GET(self):
                middleware.handle(self)
                if not hasattr(self, '_response_sent'):
                    BaseHandler.do_GET(self)
            
            def do_OPTIONS(self):
                middleware.handle(self)

        port = 50149
        server = HTTPServer(("0.0.0.0", port), WrappedHandler)
        logger.info(f"X402 Example Server running on http://0.0.0.0:{port}")
        logger.info(f"Protected paths: {self.protected_paths}")
        logger.info(f"Payment: {self.price} {self.currency} on {self.network}")
        logger.info(f"Receiver: {self.receiver}")
        server.serve_forever()


# Convenience function for quick setup
def create_x402_server(receiver: str, port: int = 8080, **kwargs):
    """Quick setup for an X402-protected server."""
    middleware = X402Middleware(
        app=lambda h: None,
        receiver=receiver,
        **kwargs
    )
    return middleware


if __name__ == "__main__":
    # Example usage
    middleware = X402Middleware(
        app=lambda h: None,
        receiver="YourSolanaAddressHere",
        protected_paths=["/ipfs/premium", "/api/premium"],
        network="solana",
        price="1",
        currency="USDC"
    )
    middleware.example()
