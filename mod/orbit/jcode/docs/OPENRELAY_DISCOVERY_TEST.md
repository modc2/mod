# OpenRelay Discovery test

This is a local-only evaluation of adding OpenRelay to `discover_tools`. It does
not modify the hosted Discovery manifest, Jcode's default endpoint, or any
production deployment.

The fixture lists OpenRelay's Rivet public Ethereum Classic JSON-RPC endpoint in
`cloud-infrastructure`. Selection is seamless for the agent: no signup, install,
API key, payment, environment variable, or MCP connection is needed. The select
response includes the endpoint, exact JSON-RPC methods, a ready-to-run request,
and hex/timestamp interpretation guidance.

## Natural test prompt

> Configure a hosted cloud-infrastructure provider for read-only Ethereum
> Classic JSON-RPC that needs no account or API key. Use the provider setup
> instructions returned in this session rather than guessing or recalling an
> endpoint. Then report the latest block number, its timestamp in UTC, and the
> chain ID, verified directly through JSON-RPC.

The prompt intentionally does not name OpenRelay, Rivet, `discover_tools`, or
the endpoint. It states the product requirement clearly enough to distinguish a
hosted infrastructure provider from a generic web-data or web-search service.

## Run

```bash
scripts/run_openrelay_discovery_test.sh
```

The runner:

1. starts the fixture on a random loopback port;
2. creates a disposable `JCODE_HOME` with the fixture endpoint;
3. copies only local provider-auth state needed for the test;
4. verifies the real read-only endpoint returns Ethereum Classic chain ID `61`;
5. exposes only `bash` and `discover_tools` to the agent;
6. validates that the agent browsed `cloud-infrastructure`, selected
   `openrelay-rivet`, ran `bash`, and answered all requested fields; and
7. deletes the temporary home and runtime directory.

Fixture unit tests:

```bash
python scripts/test_openrelay_discovery_test.py
```

The fixture sends no analytics, sponsor reports, usage events, credentials, or
prompt data to Solo Systems. Requests to the public OpenRelay/Rivet endpoint are
limited by the setup instructions to read-only JSON-RPC methods.
