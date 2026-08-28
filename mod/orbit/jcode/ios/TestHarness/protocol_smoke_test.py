#!/usr/bin/env python3
"""End-to-end protocol smoke test against the mock gateway.

Exercises: /health, /pair, ws upgrade, subscribe, get_history, message stream.
Pure stdlib so it runs anywhere. Asserts the full happy-path event sequence.
"""
import argparse
import base64
import http.client
import json
import os
import socket
import struct
import sys


def http_get(host, port, path):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def http_post(host, port, path, body):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def ws_connect(host, port, token):
    s = socket.create_connection((host, port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET /ws HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Authorization: Bearer {token}\r\n"
        "\r\n"
    )
    s.sendall(req.encode())
    # Read handshake response headers.
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(1024)
    assert b"101" in buf.split(b"\r\n")[0], buf
    return s


def ws_send(s, text):
    payload = text.encode()
    mask = os.urandom(4)
    masked = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    s.sendall(bytes(header) + mask + masked)


def ws_recv(s):
    def recv_exact(n):
        data = b""
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    b = recv_exact(2)
    opcode = b[0] & 0x0F
    length = b[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(8))[0]
    data = recv_exact(length) if length else b""
    return opcode, data


def collect_events(s, until_type, max_events=200):
    events = []
    for _ in range(max_events):
        opcode, data = ws_recv(s)
        if opcode == 0x9:  # ping -> ignore (client would pong)
            continue
        if opcode == 0x8:
            break
        text = data.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            events.append(obj)
            if obj.get("type") == until_type:
                return events
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7643)
    ap.add_argument("--code", default="123456")
    args = ap.parse_args()

    failures = []

    def check(name, cond):
        status = "PASS" if cond else "FAIL"
        print(f"  [{status}] {name}")
        if not cond:
            failures.append(name)

    # 1. health
    st, body = http_get(args.host, args.port, "/health")
    h = json.loads(body)
    check("health 200", st == 200)
    check("health status ok", h.get("status") == "ok")
    check("health gateway flag", h.get("gateway") is True)

    # 2. pair bad code
    st, body = http_post(args.host, args.port, "/pair", {"code": "000000", "device_id": "d", "device_name": "Test"})
    check("pair bad code 401", st == 401)

    # 3. pair good code
    st, body = http_post(args.host, args.port, "/pair", {"code": args.code, "device_id": "d", "device_name": "Test"})
    p = json.loads(body)
    check("pair 200", st == 200)
    token = p.get("token", "")
    check("pair returns token", bool(token))
    check("pair server_name", p.get("server_name") == "mock-jcode")

    # 4. ws connect + subscribe + history
    s = ws_connect(args.host, args.port, token)
    ws_send(s, json.dumps({"id": 1, "type": "subscribe"}))
    evs = collect_events(s, until_type="state")
    types = [e["type"] for e in evs]
    check("subscribe -> ack", "ack" in types)
    check("subscribe -> session", "session" in types)
    check("subscribe -> state", "state" in types)

    ws_send(s, json.dumps({"id": 2, "type": "get_history"}))
    evs = collect_events(s, until_type="history")
    hist = next((e for e in evs if e["type"] == "history"), None)
    check("get_history -> history", hist is not None)
    if hist:
        check("history available_models", len(hist.get("available_models", [])) >= 1)
        check("history all_sessions", len(hist.get("all_sessions", [])) >= 1)

    # 5. message -> full stream
    ws_send(s, json.dumps({"id": 3, "type": "message", "content": "hi there"}))
    evs = collect_events(s, until_type="done")
    types = [e["type"] for e in evs]
    check("message -> ack", "ack" in types)
    check("message -> reasoning_delta", "reasoning_delta" in types)
    check("message -> text_delta", "text_delta" in types)
    check("message -> tool_start", "tool_start" in types)
    check("message -> tool_exec", "tool_exec" in types)
    check("message -> tool_done", "tool_done" in types)
    check("message -> message_end", "message_end" in types)
    check("message -> tokens", "tokens" in types)
    check("message -> done", "done" in types)

    # reconstruct streamed text
    streamed = "".join(e.get("text", "") for e in evs if e["type"] == "text_delta")
    check("streamed text contains echo of input", "hi there" in streamed)
    check("streamed text has code fence", "```" in streamed)

    # 6. set_model
    ws_send(s, json.dumps({"id": 4, "type": "set_model", "model": "openai:gpt-5"}))
    evs = collect_events(s, until_type="available_models_updated")
    mc = next((e for e in evs if e["type"] == "model_changed"), None)
    check("set_model -> model_changed", mc is not None and mc.get("model") == "openai:gpt-5")

    # 7. history carries display_title + reasoning_effort (session titles UI)
    if hist:
        check("history display_title", bool(hist.get("display_title")))
        check("history reasoning_effort", bool(hist.get("reasoning_effort")))

    # 8. soft_interrupt -> injection ack precedes the streamed response
    ws_send(s, json.dumps({"id": 5, "type": "soft_interrupt",
                           "content": "queued mid-run", "urgent": False}))
    evs = collect_events(s, until_type="done")
    types = [e["type"] for e in evs]
    check("soft_interrupt -> soft_interrupt_injected", "soft_interrupt_injected" in types)
    inj = next((e for e in evs if e["type"] == "soft_interrupt_injected"), None)
    check("injected content echoes request",
          inj is not None and inj.get("content") == "queued mid-run")
    if "soft_interrupt_injected" in types and "text_delta" in types:
        check("injected before stream",
              types.index("soft_interrupt_injected") < types.index("text_delta"))

    # 9. set_reasoning_effort
    ws_send(s, json.dumps({"id": 6, "type": "set_reasoning_effort", "effort": "low"}))
    evs = collect_events(s, until_type="reasoning_effort_changed")
    rc = next((e for e in evs if e["type"] == "reasoning_effort_changed"), None)
    check("set_reasoning_effort -> reasoning_effort_changed",
          rc is not None and rc.get("effort") == "low" and rc.get("error") is None)

    # 10. compact
    ws_send(s, json.dumps({"id": 7, "type": "compact"}))
    evs = collect_events(s, until_type="compact_result")
    cr = next((e for e in evs if e["type"] == "compact_result"), None)
    check("compact -> compact_result success",
          cr is not None and cr.get("success") is True and bool(cr.get("message")))

    # 11. rename_session -> session_renamed broadcast (titles map)
    ws_send(s, json.dumps({"id": 8, "type": "rename_session", "title": "Renamed by smoke"}))
    evs = collect_events(s, until_type="session_renamed")
    rn = next((e for e in evs if e["type"] == "session_renamed"), None)
    check("rename_session -> session_renamed",
          rn is not None and rn.get("display_title") == "Renamed by smoke"
          and bool(rn.get("session_id")))

    s.close()

    print()
    if failures:
        print(f"FAILED ({len(failures)}): {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
