#!/usr/bin/env python3
"""
X402 Payment Gateway Server with Whitelist Support
Fully functional Docker-ready server
"""

import os
import json
import time
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from x402 import X402Middleware, PaymentVerification
from whitelist_manager import WhitelistManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("x402.server")

# Configuration from environment
RECEIVER = os.getenv("RECEIVER_ADDRESS", "YourSolanaAddressHere")
NETWORK = os.getenv("NETWORK", "solana")
PRICE = os.getenv("PRICE", "1")
CURRENCY = os.getenv("CURRENCY", "USDC")
FACILITATOR_URL = os.getenv("FACILITATOR_URL", "https://x402.org/facilitator")
WHITELIST_MODE = os.getenv("WHITELIST_MODE", "offchain")
WHITELIST_CONTRACT = os.getenv("WHITELIST_CONTRACT", "")
WHITELIST_RPC = os.getenv("WHITELIST_RPC", "https://mainnet.base.org")
OFFCHAIN_WHITELIST_URL = os.getenv("OFFCHAIN_WHITELIST_URL", "")
PORT = int(os.getenv("PORT", "50149"))

# Initialize whitelist manager
whitelist_manager = WhitelistManager(
    mode=WHITELIST_MODE,
    contract_address=WHITELIST_CONTRACT if WHITELIST_CONTRACT else None,
    rpc_url=WHITELIST_RPC,
    offchain_file="/app/whitelist.json",
    offchain_url=OFFCHAIN_WHITELIST_URL if OFFCHAIN_WHITELIST_URL else None,
)


def custom_verifier(payment_header: str) -> PaymentVerification:
    """
    Custom payment verifier that checks whitelist first.
    Whitelisted wallets get free access.
    """
    try:
        payment_data = json.loads(payment_header)
        wallet = payment_data.get("wallet") or payment_data.get("sender") or payment_data.get("from")
        
        if wallet and whitelist_manager.is_whitelisted(wallet):
            logger.info(f"Whitelisted wallet access: {wallet}")
            return PaymentVerification(
                valid=True,
                transaction_id="WHITELISTED",
                amount="0",
                currency=CURRENCY,
                timestamp=int(time.time())
            )
    except (json.JSONDecodeError, TypeError):
        pass
    
    return PaymentVerification(valid=False, error="Payment verification required")


# Initialize middleware
middleware = X402Middleware(
    app=lambda h: None,
    receiver=RECEIVER,
    facilitator_url=FACILITATOR_URL,
    protected_paths=["/api/premium", "/ipfs/premium", "/protected"],
    network=NETWORK,
    price=PRICE,
    currency=CURRENCY,
    custom_verifier=custom_verifier,
)


class X402Handler(BaseHTTPRequestHandler):
    """HTTP Handler with X402 payment gating and whitelist support."""
    
    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "healthy", "timestamp": int(time.time())})
            return
        
        if self.path == "/metrics":
            self._send_json(middleware.get_metrics())
            return
        
        if self.path == "/whitelist":
            self._send_json({"addresses": whitelist_manager.get_all_whitelisted()})
            return
        
        if self.path.startswith("/whitelist/check/"):
            address = self.path.split("/")[-1]
            is_wl = whitelist_manager.is_whitelisted(address)
            self._send_json({"address": address, "whitelisted": is_wl})
            return
        
        # Check if protected
        if middleware._is_protected(self.path):
            middleware.handle(self)
            if hasattr(self, '_headers_sent'):
                return
        
        # Serve content
        if self.path.startswith("/api/premium"):
            self._send_json({"data": "Premium API content", "tier": "premium"})
        elif self.path.startswith("/ipfs/premium"):
            self._send_json({"data": "Premium IPFS content", "cid": "Qm..."})
        elif self.path.startswith("/protected"):
            self._send_json({"data": "Protected resource", "access": "granted"})
        else:
            self._send_json({"data": "Public content", "tier": "free"})
    
    def do_POST(self):
        if self.path == "/whitelist/add":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                address = data.get("address")
                if address:
                    whitelist_manager.add_to_whitelist(address)
                    self._send_json({"success": True, "address": address})
                else:
                    self._send_error(400, "Address required")
            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON")
            return
        
        self._send_error(404, "Not found")
    
    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()
    
    def _send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def _send_error(self, status, message):
        self._send_json({"error": message}, status)
    
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-PAYMENT, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    
    def log_message(self, format, *args):
        logger.info(f"{self.client_address[0]} - {format % args}")


def main():
    server = HTTPServer(("0.0.0.0", PORT), X402Handler)
    logger.info(f"X402 Server running on http://0.0.0.0:{PORT}")
    logger.info(f"Receiver: {RECEIVER}")
    logger.info(f"Network: {NETWORK}, Price: {PRICE} {CURRENCY}")
    logger.info(f"Whitelist mode: {WHITELIST_MODE}")
    logger.info(f"Protected paths: {middleware.protected_paths}")
    server.serve_forever()


if __name__ == "__main__":
    main()
