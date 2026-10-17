#!/usr/bin/env python3
"""A stand-in for llama-server / ollama, so the agent loop can be tested on a
box with no weights on it.

It answers /v1/models and /v1/chat/completions (streaming and not) with a
scripted sequence of steps, which is what makes the loop's behaviour — anchor
parsing, tool execution, the finish that ends the run — testable without
5 GB of GGUF and a minute per step.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# one reply per model call, in order; the last repeats
SCRIPT = [
    '<PLAN>\n<STEP>{"tool": "bash", "params": {"command": "echo hermes-was-here"}}</STEP>\n</PLAN>',
    'All done.\n<PLAN>\n<STEP>{"tool": "finish", "params": {"summary": "ran one command"}}</STEP>\n</PLAN>',
]
CALLS = [0]


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/v1/models'):
            return self._json({'data': [{'id': 'hermes-3-8b-fake'}]})
        self._json({'error': 'nope'}, 404)

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n) or '{}')
        text = SCRIPT[min(CALLS[0], len(SCRIPT) - 1)]
        CALLS[0] += 1
        if not body.get('stream'):
            return self._json({'choices': [{'message': {'role': 'assistant',
                                                        'content': text}}]})
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        for i in range(0, len(text), 24):
            chunk = {'choices': [{'delta': {'content': text[i:i + 24]}}]}
            self.wfile.write(f'data: {json.dumps(chunk)}\n\n'.encode())
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f'fake backend on {port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
