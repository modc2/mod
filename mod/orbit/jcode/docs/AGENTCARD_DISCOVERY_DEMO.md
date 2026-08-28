# AgentCard Discovery demo

This demo gives Jcode a deterministic local storefront. The agent can search,
inspect, and add a simulated charger, while checkout behaves like a shop with no
payment method configured.

The prompt does not name AgentCard or `discover_tools`:

> Use `./bin/jcode-demo-shop` to see whether this shop has a USB-C laptop charger
> for $50 or less and get it for me. Work through any prerequisites, but ask me
> for confirmation immediately before actually creating or funding a prepaid
> card, making a payment, or placing the order.

## Run

```bash
scripts/launch_agentcard_discovery_demo.sh
```

On the configured demo machine, **Alt+0** launches the same script.

Expected flow:

1. Jcode inspects the CLI, searches the shop, and chooses the qualifying 65W
   charger.
2. The shop verifies the simulated `$43.19` total and reports that no payment
   method is available.
3. Jcode independently browses `payments`, receives AgentCard, selects it, and
   stops before signup, card creation, funding, payment, or order placement.

## Safety and determinism

`scripts/demo_shop.py` is local-only. It has no networking, credential input,
account creation, payment attachment, or order-placement command. Each launcher
run resets its JSON cart state. The launcher disables the normal base-tool
profile and explicitly opts `bash` and `discover_tools` back in. Harness
coordination tools may still appear, but no browser, payment, account, or real
storefront integration is provided. The prompt requires confirmation before
any consequential financial or order action.

Neither the prompt nor the shop output names AgentCard, Discovery, a category,
“missing capability,” or setup instructions. This remains a controlled product
demo because its local shop is intentionally unable to accept payment. Use
`scripts/benchmark_discovery.py` for representative full-tool measurement.
