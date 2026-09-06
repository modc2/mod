//! The DEX desk — trading, by asking the modules that already own the chains.
//!
//! This module holds no keys, no RPC endpoints and no wallets. Every chain in
//! this fleet already has a module that does: `eth` keeps keystore accounts and
//! signs EVM transactions, `solana` keeps an ed25519 keystore and routes through
//! Jupiter, `bt` holds Bittensor coldkeys and trades the dTAO pools. What was
//! missing was not another integration — it was the layer above them, where
//! "swap 100 USDC for ETH on Base" is one call instead of six.
//!
//! So this is a client, not a chain stack. Each tool resolves the venue, works
//! out the calldata or the argument shape, and then talks MCP to the owning
//! module over the same JSON-RPC an agent would use. The peer enforces its own
//! rules — eth refuses a mainnet write without confirm=true, solana guards on
//! USD value, bt says REAL on-chain trade — and none of those gates are
//! reimplemented here, because a second copy of a permission model is a second
//! thing to get wrong.
//!
//! Auth is passed through, never held: the caller's Authorization header goes to
//! the peer verbatim, so an agent reaches exactly the accounts it could reach by
//! calling that module directly.

use serde_json::{json, Value};

const FEE_TIERS: [u32; 3] = [500, 3000, 10000];

#[derive(Clone, Copy, PartialEq)]
pub enum Kind {
    Evm,
    Solana,
    Tao,
}

pub struct Chain {
    pub id: &'static str,
    pub label: &'static str,
    pub module: &'static str,
    /// What the owning module calls this chain.
    pub network: &'static str,
    pub kind: Kind,
    pub venue: &'static str,
    pub testnet: bool,
    pub native: &'static str,
    /// EVM only: Uniswap V3 SwapRouter02, QuoterV2, and the wrapped native token.
    pub router: &'static str,
    pub quoter: &'static str,
    pub wrapped: &'static str,
}

pub const CHAINS: &[Chain] = &[
    Chain {
        id: "ethereum",
        label: "Ethereum",
        module: "eth",
        network: "mainnet",
        kind: Kind::Evm,
        venue: "Uniswap V3",
        testnet: false,
        native: "ETH",
        router: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
        quoter: "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
        wrapped: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    },
    Chain {
        id: "base",
        label: "Base",
        module: "eth",
        network: "base",
        kind: Kind::Evm,
        venue: "Uniswap V3",
        testnet: false,
        native: "ETH",
        router: "0x2626664c2603336E57B271c5C0b26F421741e481",
        quoter: "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
        wrapped: "0x4200000000000000000000000000000000000006",
    },
    Chain {
        id: "sepolia",
        label: "Ethereum Sepolia",
        module: "eth",
        network: "sepolia",
        kind: Kind::Evm,
        venue: "Uniswap V3",
        testnet: true,
        native: "ETH",
        router: "0x3bFA4769FB09eefC5a80d6E87c3B9C650f7Ae48E",
        quoter: "0xEd1f6473345F45b75F8179591dd5bA1888cf2FB3",
        wrapped: "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
    },
    Chain {
        id: "base-sepolia",
        label: "Base Sepolia",
        module: "eth",
        network: "base-sepolia",
        kind: Kind::Evm,
        venue: "Uniswap V3",
        testnet: true,
        native: "ETH",
        router: "0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4",
        quoter: "0xC5290058841028F1614F3A6F0F5816cAd0df5E27",
        wrapped: "0x4200000000000000000000000000000000000006",
    },
    Chain {
        id: "solana",
        label: "Solana",
        module: "solana",
        network: "mainnet",
        kind: Kind::Solana,
        venue: "Jupiter (every Solana DEX)",
        testnet: false,
        native: "SOL",
        router: "",
        quoter: "",
        wrapped: "",
    },
    Chain {
        id: "tao",
        label: "Bittensor",
        module: "bt",
        network: "finney",
        kind: Kind::Tao,
        venue: "dTAO subnet pools (on-chain AMM)",
        testnet: false,
        native: "TAO",
        router: "",
        quoter: "",
        wrapped: "",
    },
];

/// Well-known tokens, so an agent can say USDC instead of an address. Anything
/// not in here is still tradable — pass the contract address (or, on Solana, the
/// mint or a symbol, which that module resolves by liquidity).
const TOKENS: &[(&str, &str, &str, u32)] = &[
    ("ethereum", "WETH", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
    ("ethereum", "USDC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
    ("ethereum", "USDT", "0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
    ("ethereum", "DAI", "0x6B175474E89094C44Da98b954EedeAC495271d0F", 18),
    ("ethereum", "WBTC", "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8),
    ("ethereum", "UNI", "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", 18),
    ("ethereum", "LINK", "0x514910771AF9Ca656af840dff83E8264EcF986CA", 18),
    ("base", "WETH", "0x4200000000000000000000000000000000000006", 18),
    ("base", "USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
    ("base", "USDbC", "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", 6),
    ("base", "DAI", "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", 18),
    ("base", "cbBTC", "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", 8),
    ("base", "AERO", "0x940181a94A35A4569E4529A3CDfB74e38FD98631", 18),
    ("sepolia", "WETH", "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14", 18),
    ("sepolia", "USDC", "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", 6),
    ("base-sepolia", "WETH", "0x4200000000000000000000000000000000000006", 18),
    ("solana", "SOL", "So11111111111111111111111111111111111111112", 9),
    ("solana", "USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
    ("solana", "USDT", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
    ("solana", "JUP", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", 6),
    ("solana", "BONK", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", 5),
];

pub fn chain(id: &str) -> Option<&'static Chain> {
    let want = id.trim().to_lowercase();
    let want = match want.as_str() {
        "eth" | "mainnet" | "ethereum" | "l1" => "ethereum".to_string(),
        "bittensor" | "tao" | "finney" | "subtensor" => "tao".to_string(),
        "sol" | "solana" => "solana".to_string(),
        other => other.to_string(),
    };
    CHAINS.iter().find(|c| c.id == want)
}

// ── the peer client ────────────────────────────────────────────────────────

pub struct Dex {
    http: reqwest::Client,
    pub eth: String,
    pub solana: String,
    pub bt: String,
    /// The scale-to-zero proxy. A module-to-module call on a direct port never
    /// wakes a slept dependency, so a refused connection gets one knock here
    /// before it is reported as down.
    pub activator: String,
}

impl Dex {
    pub fn from_env() -> Self {
        Self {
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(180))
                .build()
                .expect("http client"),
            eth: env("DEFI_ETH_URL", "http://localhost:50730"),
            solana: env("DEFI_SOLANA_URL", "http://localhost:50710"),
            bt: env("DEFI_BT_URL", "http://localhost:50280"),
            activator: env("DEFI_ACTIVATOR_URL", "http://localhost:9000"),
        }
    }

    pub fn base(&self, module: &str) -> &str {
        match module {
            "eth" => &self.eth,
            "solana" => &self.solana,
            _ => &self.bt,
        }
    }

    /// One MCP tool call on a peer module, with the caller's token attached.
    pub async fn peer(
        &self,
        module: &str,
        tool: &str,
        args: Value,
        token: Option<&str>,
    ) -> Result<Value, String> {
        let body = json!({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": { "name": tool, "arguments": args }
        });
        let response = match self.post(module, &body, token).await {
            Ok(r) => r,
            Err(first) => {
                // Slept, most likely. Knock through the activator and retry once.
                self.wake(module).await;
                self.post(module, &body, token)
                    .await
                    .map_err(|second| format!("{module} is not answering ({first}; after waking it: {second}) — start it with `m {module}/serve`"))?
            }
        };
        unwrap_tool_result(response, module, tool)
    }

    async fn post(&self, module: &str, body: &Value, token: Option<&str>) -> Result<Value, String> {
        let mut req = self
            .http
            .post(format!("{}/mcp", self.base(module)))
            .header("accept", "application/json, text/event-stream")
            .json(body);
        if let Some(t) = token {
            req = req.header("authorization", format!("Bearer {t}"));
        }
        let response = req.send().await.map_err(|e| e.to_string())?;
        let status = response.status().as_u16();
        let text = response.text().await.map_err(|e| e.to_string())?;
        if status >= 400 && text.trim().is_empty() {
            return Err(format!("HTTP {status}"));
        }
        parse_body(&text).ok_or_else(|| format!("HTTP {status}: {}", snippet(&text)))
    }

    async fn wake(&self, module: &str) {
        if self.activator.is_empty() {
            return;
        }
        let _ = self
            .http
            .get(format!("{}/api/{module}/health", self.activator))
            .timeout(std::time::Duration::from_secs(20))
            .send()
            .await;
    }

    /// Every venue this desk can reach, and — with `check` — whether the module
    /// behind it is actually up right now.
    pub async fn venues(&self, check: bool) -> Value {
        let mut modules = serde_json::Map::new();
        if check {
            for module in ["eth", "solana", "bt"] {
                modules.insert(module.to_string(), self.probe(module).await);
            }
        }
        json!({
            "venues": CHAINS.iter().map(|c| json!({
                "chain": c.id,
                "label": c.label,
                "venue": c.venue,
                "module": c.module,
                "mcp": format!("{}/mcp", self.base(c.module)),
                "network": c.network,
                "testnet": c.testnet,
                "native": c.native,
                "router": if c.router.is_empty() { Value::Null } else { json!(c.router) },
                "trades": match c.kind {
                    Kind::Evm => "any ERC-20 pair with a Uniswap V3 pool; symbols below, or an address",
                    Kind::Solana => "any SPL token Jupiter can route; a mint, or a symbol it resolves by liquidity",
                    Kind::Tao => "TAO against a subnet's alpha token — sell/buy take TAO or SN<netuid>",
                },
            })).collect::<Vec<_>>(),
            "modules": modules,
            "auth": "your Authorization header is forwarded to the owning module unchanged — \
                     sign in to eth/solana/bt as you normally would, or pass auth=<token> per call",
            "rule": "this module never holds a key. The chain module signs, and its own \
                     guard (confirm=true on mainnet, USD ceilings) is the one that applies.",
        })
    }

    async fn probe(&self, module: &str) -> Value {
        let body = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });
        let attempt = self.post(module, &body, None).await;
        let attempt = match attempt {
            Ok(v) => Ok(v),
            Err(_) => {
                self.wake(module).await;
                self.post(module, &body, None).await
            }
        };
        match attempt {
            Ok(v) => json!({
                "reachable": true,
                "url": self.base(module),
                "tools": v.pointer("/result/tools").and_then(|t| t.as_array()).map(|t| t.len()),
            }),
            Err(e) => json!({
                "reachable": false,
                "url": self.base(module),
                "error": e,
                "hint": format!("start it with `m {module}/serve`, or point DEFI_{}_URL at it",
                                module.to_uppercase()),
            }),
        }
    }

    // ── tokens ─────────────────────────────────────────────────────────────

    pub fn tokens(&self, want: Option<&str>) -> Result<Value, String> {
        let chains: Vec<&Chain> = match want {
            Some(id) => vec![chain(id).ok_or_else(|| unknown_chain(id))?],
            None => CHAINS.iter().collect(),
        };
        Ok(json!({
            "tokens": chains.iter().map(|c| json!({
                "chain": c.id,
                "native": c.native,
                "known": TOKENS.iter().filter(|t| t.0 == c.id).map(|t| json!({
                    "symbol": t.1, "address": t.2, "decimals": t.3
                })).collect::<Vec<_>>(),
                "anything_else": match c.kind {
                    Kind::Evm => "pass the contract address — decimals are read on chain",
                    Kind::Solana => "pass the mint, or a symbol Jupiter resolves",
                    Kind::Tao => "subnets are the assets: 'TAO' or 'SN<netuid>' (SN64, SN8…). \
                                  bt_subnets lists them.",
                },
            })).collect::<Vec<_>>()
        }))
    }

    // ── quoting ────────────────────────────────────────────────────────────

    pub async fn quote(&self, args: &Value, token: Option<&str>) -> Result<Value, String> {
        let spec = chain(&arg_str(args, "chain")?).ok_or_else(|| {
            unknown_chain(&arg_str(args, "chain").unwrap_or_default())
        })?;
        let sell = arg_str(args, "sell")?;
        let buy = arg_str(args, "buy")?;
        let amount = arg_amount(args)?;
        let slippage = args
            .get("slippageBps")
            .or_else(|| args.get("slippage_bps"))
            .and_then(|v| v.as_f64())
            .unwrap_or(50.0) as u64;

        match spec.kind {
            Kind::Evm => self.evm_quote(spec, &sell, &buy, &amount, slippage, token).await,
            Kind::Solana => {
                let quoted = self
                    .peer(
                        "solana",
                        "sol_quote",
                        json!({ "input": sell, "output": buy, "amount": amount.parse::<f64>().map_err(|_| "amount must be a number")?, "slippage_bps": slippage }),
                        token,
                    )
                    .await?;
                Ok(json!({
                    "chain": spec.id, "venue": spec.venue, "module": "solana",
                    "sell": quoted.get("sell"), "buy": quoted.get("buy"),
                    "rate": quoted.get("rate"),
                    "min_received": quoted.get("worst_case_out"),
                    "price_impact_pct": quoted.get("price_impact_pct"),
                    "slippage_bps": slippage,
                    "route": quoted.get("route"),
                    "quoted_by": "sol_quote",
                }))
            }
            Kind::Tao => self.tao_quote(spec, &sell, &buy, &amount, slippage, token).await,
        }
    }

    async fn evm_quote(
        &self,
        spec: &Chain,
        sell: &str,
        buy: &str,
        amount: &str,
        slippage: u64,
        token: Option<&str>,
    ) -> Result<Value, String> {
        let sell_token = self.resolve_evm(spec, sell, token).await?;
        let buy_token = self.resolve_evm(spec, buy, token).await?;
        if sell_token.address.eq_ignore_ascii_case(&buy_token.address) {
            return Err("both sides of the trade are the same token".into());
        }
        let amount_in = to_base_units(amount, sell_token.decimals)?;

        let mut best: Option<(u128, Value, Vec<u32>)> = None;
        let mut tried = Vec::new();
        for fee in FEE_TIERS {
            let call = json!({
                "address": spec.quoter,
                "function": "quoteExactInputSingle",
                "network": spec.network,
                "abi": quoter_abi(),
                "args": [[sell_token.address, buy_token.address, amount_in.to_string(), fee, 0]],
            });
            match self.peer("eth", "eth_read", call, token).await {
                Ok(v) => {
                    let out = first_uint(v.get("result")).unwrap_or(0);
                    tried.push(json!({ "fee_bps": fee as f64 / 100.0, "out": out.to_string() }));
                    if out > 0 && best.as_ref().map(|b| out > b.0).unwrap_or(true) {
                        best = Some((
                            out,
                            json!([{ "pool": format!("{}/{} {}bps", sell_token.symbol, buy_token.symbol, fee as f64 / 100.0) }]),
                            vec![fee],
                        ));
                    }
                }
                Err(e) => tried.push(json!({ "fee_bps": fee as f64 / 100.0, "error": snippet(&e) })),
            }
        }

        // No direct pool? Try the route everything else takes: through wrapped native.
        if best.is_none()
            && !sell_token.address.eq_ignore_ascii_case(spec.wrapped)
            && !buy_token.address.eq_ignore_ascii_case(spec.wrapped)
        {
            if let Some((out, fees)) = self
                .evm_hop_quote(spec, &sell_token.address, &buy_token.address, &amount_in, token)
                .await
            {
                best = Some((
                    out,
                    json!([
                        { "pool": format!("{}/W{} {}bps", sell_token.symbol, spec.native, fees[0] as f64 / 100.0) },
                        { "pool": format!("W{}/{} {}bps", spec.native, buy_token.symbol, fees[1] as f64 / 100.0) }
                    ]),
                    fees,
                ));
            }
        }

        let Some((out, route, fees)) = best else {
            return Err(format!(
                "no Uniswap V3 pool on {} priced {} → {} at any fee tier ({}). \
                 Check the addresses, or try a smaller size.",
                spec.label,
                sell_token.symbol,
                buy_token.symbol,
                serde_json::to_string(&tried).unwrap_or_default()
            ));
        };

        let min_out = out * (10_000u128.saturating_sub(slippage as u128)) / 10_000;
        Ok(json!({
            "chain": spec.id, "venue": spec.venue, "module": "eth", "network": spec.network,
            "sell": { "symbol": sell_token.symbol, "address": sell_token.address,
                      "amount": amount, "base_units": amount_in.to_string(),
                      "native": sell_token.native },
            "buy": { "symbol": buy_token.symbol, "address": buy_token.address,
                     "amount": from_base_units(out, buy_token.decimals),
                     "base_units": out.to_string(), "native": buy_token.native },
            "rate": rate(amount, out, buy_token.decimals),
            "min_received": from_base_units(min_out, buy_token.decimals),
            "min_received_base_units": min_out.to_string(),
            "slippage_bps": slippage,
            "route": route,
            "fee_tiers": fees,
            "tried": tried,
            "quoted_by": format!("QuoterV2 {} via eth_read", spec.quoter),
        }))
    }

    /// Both legs of a token → wrapped native → token route, priced together.
    async fn evm_hop_quote(
        &self,
        spec: &Chain,
        sell: &str,
        buy: &str,
        amount_in: &u128,
        token: Option<&str>,
    ) -> Option<(u128, Vec<u32>)> {
        let mut first: Option<(u128, u32)> = None;
        for fee in FEE_TIERS {
            let call = json!({
                "address": spec.quoter, "function": "quoteExactInputSingle",
                "network": spec.network, "abi": quoter_abi(),
                "args": [[sell, spec.wrapped, amount_in.to_string(), fee, 0]],
            });
            if let Ok(v) = self.peer("eth", "eth_read", call, token).await {
                let out = first_uint(v.get("result")).unwrap_or(0);
                if out > 0 && first.map(|f| out > f.0).unwrap_or(true) {
                    first = Some((out, fee));
                }
            }
        }
        let (mid, first_fee) = first?;
        let mut second: Option<(u128, u32)> = None;
        for fee in FEE_TIERS {
            let call = json!({
                "address": spec.quoter, "function": "quoteExactInputSingle",
                "network": spec.network, "abi": quoter_abi(),
                "args": [[spec.wrapped, buy, mid.to_string(), fee, 0]],
            });
            if let Ok(v) = self.peer("eth", "eth_read", call, token).await {
                let out = first_uint(v.get("result")).unwrap_or(0);
                if out > 0 && second.map(|s| out > s.0).unwrap_or(true) {
                    second = Some((out, fee));
                }
            }
        }
        let (out, second_fee) = second?;
        Some((out, vec![first_fee, second_fee]))
    }

    async fn tao_quote(
        &self,
        spec: &Chain,
        sell: &str,
        buy: &str,
        amount: &str,
        slippage: u64,
        token: Option<&str>,
    ) -> Result<Value, String> {
        let (netuid, side) = tao_side(sell, buy)?;
        let pool = self
            .peer("bt", "bt_price", json!({ "netuid": netuid, "network": spec.network }), token)
            .await?;
        let tao_in = number(pool.get("tao_in"))
            .or_else(|| number(pool.get("tao_reserve")))
            .or_else(|| number(pool.pointer("/pool/tao_in")));
        let alpha_in = number(pool.get("alpha_in"))
            .or_else(|| number(pool.get("alpha_reserve")))
            .or_else(|| number(pool.pointer("/pool/alpha_in")));
        let price = number(pool.get("price")).or_else(|| number(pool.pointer("/price/tao")));
        let size: f64 = amount.parse().map_err(|_| "amount must be a number")?;

        // Constant product against the live reserves when the pool is legible,
        // the spot price when it is not — and the answer says which it was.
        let (out, impact, basis) = match (tao_in, alpha_in, side) {
            (Some(t), Some(a), TaoSide::Buy) if t > 0.0 && a > 0.0 => {
                let got = a - (t * a) / (t + size);
                (got, (1.0 - got / (size * (a / t))) * 100.0, "constant product on live reserves")
            }
            (Some(t), Some(a), TaoSide::Sell) if t > 0.0 && a > 0.0 => {
                let alpha = if let Some(p) = price { size / p.max(1e-18) } else { size };
                let got = t - (t * a) / (a + alpha);
                (got, (1.0 - got / size) * 100.0, "constant product on live reserves")
            }
            _ => {
                let p = price.ok_or("bt_price did not return a price for that subnet")?;
                match side {
                    TaoSide::Buy => (size / p.max(1e-18), 0.0, "spot price — reserves unavailable"),
                    TaoSide::Sell => (size, 0.0, "spot price — reserves unavailable"),
                }
            }
        };
        let min_out = out * (1.0 - slippage as f64 / 10_000.0);
        Ok(json!({
            "chain": spec.id, "venue": spec.venue, "module": "bt", "network": spec.network,
            "netuid": netuid,
            "sell": { "symbol": if side == TaoSide::Buy { "TAO".into() } else { format!("SN{netuid} alpha") },
                      "amount": size,
                      // bt_sell sizes an unstake in TAO-equivalent, not in alpha,
                      // and a number whose unit is guessed is a wrong trade.
                      "units": if side == TaoSide::Buy { "TAO" } else { "TAO-equivalent of alpha" } },
            "buy": { "symbol": if side == TaoSide::Buy { format!("SN{netuid} alpha") } else { "TAO".into() },
                     "amount": round9(out) },
            "rate": round9(out / size.max(1e-18)),
            "min_received": round9(min_out),
            "price_impact_pct": round9(impact.max(0.0)),
            "slippage_bps": slippage,
            "basis": basis,
            "pool": pool,
            "quoted_by": "bt_price",
            "note": if side == TaoSide::Buy {
                "dTAO trades are staking operations: buying stakes TAO into the subnet's pool \
                 and the alpha you receive earns while it is held."
            } else {
                "selling unstakes. `amount` is TAO-equivalent — the size bt_sell takes — not a \
                 number of alpha tokens."
            },
        }))
    }

    // ── trading ────────────────────────────────────────────────────────────

    pub async fn swap(&self, args: &Value, token: Option<&str>) -> Result<Value, String> {
        let id = arg_str(args, "chain")?;
        let spec = chain(&id).ok_or_else(|| unknown_chain(&id))?;
        let confirm = args.get("confirm").and_then(|c| c.as_bool()).unwrap_or(false);
        let dry_run = args.get("dryRun").or_else(|| args.get("dry_run"))
            .and_then(|c| c.as_bool()).unwrap_or(false);
        let quoted = self.quote(args, token).await?;

        if dry_run {
            return Ok(json!({
                "traded": false, "dry_run": true, "quote": quoted,
                "reason": "dry_run=true — this is the trade that would have been signed",
            }));
        }
        if !spec.testnet && !confirm {
            return Ok(json!({
                "traded": false,
                "needs_confirm": true,
                "quote": quoted,
                "reason": format!("{} is real money. Call again with confirm=true to trade it.",
                                  spec.label),
            }));
        }

        match spec.kind {
            Kind::Evm => self.evm_swap(spec, args, &quoted, confirm, token).await,
            Kind::Solana => {
                let out = self
                    .peer(
                        "solana",
                        "sol_swap",
                        json!({
                            "input": arg_str(args, "sell")?,
                            "output": arg_str(args, "buy")?,
                            "amount": arg_amount(args)?.parse::<f64>().map_err(|_| "amount must be a number")?,
                            "slippage_bps": args.get("slippageBps").and_then(|v| v.as_f64()).unwrap_or(50.0),
                            "wallet": args.get("account").or_else(|| args.get("wallet")),
                            "confirm": confirm,
                        }),
                        token,
                    )
                    .await?;
                Ok(json!({ "traded": out.get("sent") == Some(&json!(true)), "chain": spec.id,
                           "venue": spec.venue, "executed_by": "sol_swap", "result": out }))
            }
            Kind::Tao => {
                let (netuid, side) = tao_side(&arg_str(args, "sell")?, &arg_str(args, "buy")?)?;
                let amount: f64 = arg_amount(args)?.parse().map_err(|_| "amount must be a number")?;
                let mut call = json!({
                    "netuid": netuid, "amount_tao": amount, "network": spec.network,
                });
                if let Some(w) = args.get("account").or_else(|| args.get("wallet")) {
                    call["wallet"] = w.clone();
                }
                if let Some(h) = args.get("hotkey") {
                    call["hotkey"] = h.clone();
                }
                let tool = match side {
                    TaoSide::Buy => "bt_buy",
                    TaoSide::Sell => "bt_sell",
                };
                let out = self.peer("bt", tool, call, token).await?;
                Ok(json!({ "traded": true, "chain": spec.id, "venue": spec.venue,
                           "executed_by": tool, "quote": quoted, "result": out }))
            }
        }
    }

    async fn evm_swap(
        &self,
        spec: &Chain,
        args: &Value,
        quoted: &Value,
        confirm: bool,
        token: Option<&str>,
    ) -> Result<Value, String> {
        let account = arg_str(args, "account").map_err(|_| {
            "'account' is required on an EVM chain — the name of an eth-module account \
             (eth_accounts lists yours, eth_unlock opens it)"
                .to_string()
        })?;
        let owner = self.evm_address(&account, token).await?;

        let sell_address = string_at(quoted, "/sell/address")?;
        let buy_address = string_at(quoted, "/buy/address")?;
        let amount_in = string_at(quoted, "/sell/base_units")?;
        let min_out = string_at(quoted, "/min_received_base_units")?;
        let native_in = quoted.pointer("/sell/native").and_then(|v| v.as_bool()).unwrap_or(false);
        let fees: Vec<u64> = quoted
            .get("fee_tiers")
            .and_then(|f| f.as_array())
            .map(|f| f.iter().filter_map(|v| v.as_u64()).collect())
            .unwrap_or_default();

        // An ERC-20 has to be allowed out of the wallet before the router can
        // pull it. Native currency rides along as msg.value and needs nothing.
        let mut approval = Value::Null;
        if !native_in {
            let allowance = self
                .peer(
                    "eth",
                    "eth_read",
                    json!({ "address": sell_address, "function": "allowance",
                            "network": spec.network, "abi": erc20_abi(),
                            "args": [owner, spec.router] }),
                    token,
                )
                .await
                .ok()
                .and_then(|v| first_uint(v.get("result")))
                .unwrap_or(0);
            if allowance < amount_in.parse::<u128>().unwrap_or(u128::MAX) {
                approval = self
                    .peer(
                        "eth",
                        "eth_approve",
                        json!({ "account": account, "token": sell_address,
                                "spender": spec.router, "amount": format!("{amount_in}wei"),
                                "network": spec.network, "confirm": confirm,
                                "password": args.get("password") }),
                        token,
                    )
                    .await
                    .map_err(|e| format!("the approval failed, so nothing was traded: {e}"))?;
            }
        }

        let (function, params) = if fees.len() > 1 {
            let path = v3_path(&sell_address, spec.wrapped, &buy_address, &fees)?;
            (
                "exactInput",
                json!([[path, owner, amount_in, min_out]]),
            )
        } else {
            let fee = fees.first().copied().unwrap_or(3000);
            (
                "exactInputSingle",
                json!([[sell_address, buy_address, fee, owner, amount_in, min_out, 0]]),
            )
        };

        let mut call = json!({
            "account": account,
            "address": spec.router,
            "function": function,
            "args": params,
            "network": spec.network,
            "abi": router_abi(),
            "confirm": confirm,
        });
        if native_in {
            call["value"] = json!(format!("{amount_in}wei"));
        }
        if let Some(p) = args.get("password") {
            call["password"] = p.clone();
        }
        let out = self.peer("eth", "eth_write", call, token).await?;
        Ok(json!({
            "traded": true,
            "chain": spec.id, "venue": spec.venue, "network": spec.network,
            "executed_by": format!("eth_write {function} on SwapRouter02 {}", spec.router),
            "quote": quoted,
            "approval": approval,
            "result": out,
            // Buying "ETH" through a router gets you the wrapped token. Saying so
            // is the difference between a surprise and a second call.
            "note": if quoted.pointer("/buy/native").and_then(|v| v.as_bool()).unwrap_or(false) {
                json!(format!("this delivered W{0}, not {0} — unwrap it with eth_write \
                               withdraw() on {1}", spec.native, spec.wrapped))
            } else { Value::Null },
        }))
    }

    /// The address behind an eth-module account name.
    pub async fn evm_address(&self, account: &str, token: Option<&str>) -> Result<String, String> {
        if account.starts_with("0x") && account.len() == 42 {
            return Ok(account.to_string());
        }
        let accounts = self.peer("eth", "eth_accounts", json!({}), token).await?;
        let list = accounts
            .get("accounts")
            .and_then(|a| a.as_array())
            .cloned()
            .unwrap_or_default();
        list.iter()
            .find(|a| a.get("name").and_then(|n| n.as_str()) == Some(account))
            .and_then(|a| a.get("address").and_then(|x| x.as_str()).map(|s| s.to_string()))
            .ok_or_else(|| {
                format!(
                    "no eth account named '{account}' — yours are [{}]. \
                     Create one with eth_new_account, then fund it.",
                    list.iter()
                        .filter_map(|a| a.get("name").and_then(|n| n.as_str()))
                        .collect::<Vec<_>>()
                        .join(", ")
                )
            })
    }

    async fn resolve_evm(
        &self,
        spec: &Chain,
        symbol: &str,
        token: Option<&str>,
    ) -> Result<EvmToken, String> {
        let want = symbol.trim();
        if want.eq_ignore_ascii_case(spec.native) || want.eq_ignore_ascii_case("native") {
            return Ok(EvmToken {
                symbol: spec.native.to_string(),
                address: spec.wrapped.to_string(),
                decimals: 18,
                native: true,
            });
        }
        if let Some(t) = TOKENS
            .iter()
            .find(|t| t.0 == spec.id && t.1.eq_ignore_ascii_case(want))
        {
            return Ok(EvmToken {
                symbol: t.1.to_string(),
                address: t.2.to_string(),
                decimals: t.3,
                native: false,
            });
        }
        if !want.starts_with("0x") || want.len() != 42 {
            let known: Vec<&str> = TOKENS.iter().filter(|t| t.0 == spec.id).map(|t| t.1).collect();
            return Err(format!(
                "'{want}' is not a token this desk knows on {} — pass its contract address, \
                 or use one of [{}, {}]",
                spec.label,
                spec.native,
                known.join(", ")
            ));
        }
        // An address it has never seen: ask the chain what it is.
        let meta = self
            .peer(
                "eth",
                "eth_token",
                json!({ "token": want, "network": spec.network }),
                token,
            )
            .await
            .map_err(|e| format!("{want} does not read as an ERC-20 on {}: {e}", spec.label))?;
        Ok(EvmToken {
            symbol: meta
                .get("symbol")
                .and_then(|s| s.as_str())
                .unwrap_or("token")
                .to_string(),
            address: meta
                .get("address")
                .and_then(|s| s.as_str())
                .unwrap_or(want)
                .to_string(),
            decimals: meta.get("decimals").and_then(|d| d.as_u64()).unwrap_or(18) as u32,
            native: false,
        })
    }

    // ── what you are holding ───────────────────────────────────────────────

    pub async fn balances(&self, args: &Value, token: Option<&str>) -> Result<Value, String> {
        let id = arg_str(args, "chain")?;
        let spec = chain(&id).ok_or_else(|| unknown_chain(&id))?;
        let who = args
            .get("address")
            .or_else(|| args.get("account"))
            .or_else(|| args.get("wallet"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        match spec.kind {
            Kind::Evm => {
                let mut call = json!({ "networks": [spec.network] });
                if !who.is_empty() {
                    call["address"] = json!(who);
                }
                let out = self.peer("eth", "eth_portfolio", call, token).await?;
                Ok(json!({ "chain": spec.id, "read_by": "eth_portfolio", "holdings": out }))
            }
            Kind::Solana => {
                if who.is_empty() {
                    return Err("pass address= — the Solana wallet to read".into());
                }
                let out = self
                    .peer("solana", "sol_portfolio", json!({ "address": who }), token)
                    .await?;
                Ok(json!({ "chain": spec.id, "read_by": "sol_portfolio", "holdings": out }))
            }
            Kind::Tao => {
                let mut call = json!({ "network": spec.network });
                if !who.is_empty() {
                    call["wallet"] = json!(who);
                }
                if let Some(h) = args.get("hotkey") {
                    call["hotkey"] = h.clone();
                }
                let out = self.peer("bt", "bt_portfolio", call, token).await?;
                Ok(json!({ "chain": spec.id, "read_by": "bt_portfolio", "holdings": out }))
            }
        }
    }
}

pub struct EvmToken {
    pub symbol: String,
    pub address: String,
    pub decimals: u32,
    pub native: bool,
}

#[derive(PartialEq, Clone, Copy)]
enum TaoSide {
    Buy,
    Sell,
}

/// "TAO" → "SN64" is a buy; "SN64" → "TAO" is a sell. Anything else is a pair
/// the subtensor AMM cannot express in one hop.
fn tao_side(sell: &str, buy: &str) -> Result<(u64, TaoSide), String> {
    let netuid_of = |s: &str| -> Option<u64> {
        let t = s.trim().to_lowercase();
        let t = t.strip_prefix("sn").or_else(|| t.strip_prefix("subnet")).unwrap_or(&t);
        t.trim_start_matches([' ', '-', '_']).parse::<u64>().ok()
    };
    let is_tao = |s: &str| {
        let t = s.trim().to_lowercase();
        t == "tao" || t == "root" || t == "sn0" || t == "0"
    };
    match (is_tao(sell), is_tao(buy)) {
        (true, false) => netuid_of(buy)
            .map(|n| (n, TaoSide::Buy))
            .ok_or_else(|| format!("'{buy}' is not a subnet — say SN64, or a netuid")),
        (false, true) => netuid_of(sell)
            .map(|n| (n, TaoSide::Sell))
            .ok_or_else(|| format!("'{sell}' is not a subnet — say SN64, or a netuid")),
        (true, true) => Err("TAO for TAO is not a trade".into()),
        (false, false) => Err(
            "on Bittensor one side has to be TAO — a subnet-to-subnet move is bt_swap, \
             which the bt module exposes directly"
                .into(),
        ),
    }
}

// ── ABIs, small and hand-written, because these four functions are the whole
//    surface this desk needs and a full router ABI is 40kB of noise ──────────

fn quoter_abi() -> Value {
    json!([{
        "name": "quoteExactInputSingle", "type": "function", "stateMutability": "view",
        "inputs": [{ "name": "params", "type": "tuple", "components": [
            { "name": "tokenIn", "type": "address" },
            { "name": "tokenOut", "type": "address" },
            { "name": "amountIn", "type": "uint256" },
            { "name": "fee", "type": "uint24" },
            { "name": "sqrtPriceLimitX96", "type": "uint160" }]}],
        "outputs": [
            { "name": "amountOut", "type": "uint256" },
            { "name": "sqrtPriceX96After", "type": "uint160" },
            { "name": "initializedTicksCrossed", "type": "uint32" },
            { "name": "gasEstimate", "type": "uint256" }]
    }])
}

fn router_abi() -> Value {
    json!([
        {
            "name": "exactInputSingle", "type": "function", "stateMutability": "payable",
            "inputs": [{ "name": "params", "type": "tuple", "components": [
                { "name": "tokenIn", "type": "address" },
                { "name": "tokenOut", "type": "address" },
                { "name": "fee", "type": "uint24" },
                { "name": "recipient", "type": "address" },
                { "name": "amountIn", "type": "uint256" },
                { "name": "amountOutMinimum", "type": "uint256" },
                { "name": "sqrtPriceLimitX96", "type": "uint160" }]}],
            "outputs": [{ "name": "amountOut", "type": "uint256" }]
        },
        {
            "name": "exactInput", "type": "function", "stateMutability": "payable",
            "inputs": [{ "name": "params", "type": "tuple", "components": [
                { "name": "path", "type": "bytes" },
                { "name": "recipient", "type": "address" },
                { "name": "amountIn", "type": "uint256" },
                { "name": "amountOutMinimum", "type": "uint256" }]}],
            "outputs": [{ "name": "amountOut", "type": "uint256" }]
        }
    ])
}

pub fn erc20_abi() -> Value {
    json!([
        { "name": "allowance", "type": "function", "stateMutability": "view",
          "inputs": [{ "name": "owner", "type": "address" }, { "name": "spender", "type": "address" }],
          "outputs": [{ "name": "", "type": "uint256" }] },
        { "name": "balanceOf", "type": "function", "stateMutability": "view",
          "inputs": [{ "name": "account", "type": "address" }],
          "outputs": [{ "name": "", "type": "uint256" }] },
        { "name": "decimals", "type": "function", "stateMutability": "view",
          "inputs": [], "outputs": [{ "name": "", "type": "uint8" }] }
    ])
}

/// tokenIn · fee · WETH · fee · tokenOut, packed the way a V3 path is.
fn v3_path(sell: &str, wrapped: &str, buy: &str, fees: &[u64]) -> Result<String, String> {
    let hex = |a: &str| -> Result<String, String> {
        let clean = a.trim_start_matches("0x");
        if clean.len() != 40 {
            return Err(format!("{a} is not an address"));
        }
        Ok(clean.to_lowercase())
    };
    if fees.len() < 2 {
        return Err("a multi-hop path needs two fee tiers".into());
    }
    Ok(format!(
        "0x{}{:06x}{}{:06x}{}",
        hex(sell)?,
        fees[0],
        hex(wrapped)?,
        fees[1],
        hex(buy)?
    ))
}

// ── plumbing ───────────────────────────────────────────────────────────────

fn env(key: &str, fallback: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| fallback.to_string())
}

pub fn unknown_chain(id: &str) -> String {
    format!(
        "'{id}' is not a chain this desk trades — {}",
        CHAINS.iter().map(|c| c.id).collect::<Vec<_>>().join(", ")
    )
}

pub fn arg_str(args: &Value, key: &str) -> Result<String, String> {
    args.get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("'{key}' is required"))
}

/// Amounts arrive as `"1.5"` or `1.5`, and both have to survive as digits —
/// a float that has been through JSON is not what anyone typed.
fn arg_amount(args: &Value) -> Result<String, String> {
    match args.get("amount") {
        Some(Value::String(s)) if !s.trim().is_empty() => Ok(s.trim().to_string()),
        Some(Value::Number(n)) => Ok(n.to_string()),
        _ => Err("'amount' is required — how much of `sell` to sell".into()),
    }
}

fn snippet(text: &str) -> String {
    let clean = text.trim().replace('\n', " ");
    if clean.len() > 240 {
        format!("{}…", &clean[..240])
    } else {
        clean
    }
}

/// MCP over HTTP answers as JSON or as one SSE frame; both carry the same body.
fn parse_body(text: &str) -> Option<Value> {
    if let Ok(v) = serde_json::from_str::<Value>(text) {
        return Some(v);
    }
    text.lines()
        .filter_map(|l| l.strip_prefix("data:"))
        .find_map(|l| serde_json::from_str::<Value>(l.trim()).ok())
}

/// Unwrap the tool result: MCP puts the payload in content[0].text as a string,
/// and signals failure with isError rather than a JSON-RPC error.
fn unwrap_tool_result(response: Value, module: &str, tool: &str) -> Result<Value, String> {
    if let Some(e) = response.pointer("/error/message").and_then(|m| m.as_str()) {
        return Err(format!("{module}.{tool}: {e}"));
    }
    let result = response
        .get("result")
        .ok_or_else(|| format!("{module}.{tool} answered nothing usable"))?;
    let text = result
        .pointer("/content/0/text")
        .and_then(|t| t.as_str())
        .unwrap_or("");
    let value: Value = serde_json::from_str(text).unwrap_or(Value::String(text.to_string()));
    let failed = result
        .get("isError")
        .and_then(|e| e.as_bool())
        .unwrap_or(false);
    if failed {
        let why = value
            .get("error")
            .and_then(|e| e.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| snippet(text));
        return Err(format!("{module}.{tool}: {why}"));
    }
    Ok(value)
}

pub fn string_at(value: &Value, pointer: &str) -> Result<String, String> {
    value
        .pointer(pointer)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| format!("the quote is missing {pointer}"))
}

fn number(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.parse().ok(),
        _ => None,
    }
}

/// A uint out of an ABI decode. Big values arrive as JSON integers wider than
/// an f64 can hold, so they are read from the literal digits, not through one.
pub fn first_uint(value: Option<&Value>) -> Option<u128> {
    match value? {
        Value::Array(items) => items.first().and_then(|v| first_uint(Some(v))),
        Value::Number(n) => n.to_string().parse::<u128>().ok(),
        Value::String(s) => {
            let t = s.trim();
            if let Some(hex) = t.strip_prefix("0x") {
                u128::from_str_radix(hex, 16).ok()
            } else {
                t.parse::<u128>().ok()
            }
        }
        _ => None,
    }
}

/// "1.5" at 6 decimals → 1500000. Done on the digits: a float would round the
/// bottom of an 18-decimal amount into somebody else's money.
pub fn to_base_units(amount: &str, decimals: u32) -> Result<u128, String> {
    let text = amount.trim().replace(['_', ','], "");
    if text.is_empty() {
        return Err("amount is empty".into());
    }
    let (whole, frac) = match text.split_once('.') {
        Some((w, f)) => (w, f),
        None => (text.as_str(), ""),
    };
    if !whole.chars().all(|c| c.is_ascii_digit()) || !frac.chars().all(|c| c.is_ascii_digit()) {
        return Err(format!("'{amount}' is not a positive decimal amount"));
    }
    let decimals = decimals as usize;
    if frac.len() > decimals {
        // Silently truncating dust would be a lie about what was traded.
        if frac[decimals..].chars().any(|c| c != '0') {
            return Err(format!(
                "'{amount}' has more precision than this token has decimals ({decimals})"
            ));
        }
    }
    let mut digits = String::from(whole);
    let padded: String = frac.chars().chain(std::iter::repeat('0')).take(decimals).collect();
    digits.push_str(&padded);
    let trimmed = digits.trim_start_matches('0');
    let value = if trimmed.is_empty() { "0" } else { trimmed };
    let units: u128 = value
        .parse()
        .map_err(|_| format!("'{amount}' is too large to express in base units"))?;
    if units == 0 {
        return Err("amount rounds to zero at this token's precision".into());
    }
    Ok(units)
}

pub fn from_base_units(units: u128, decimals: u32) -> String {
    let text = units.to_string();
    let decimals = decimals as usize;
    if decimals == 0 {
        return text;
    }
    let padded = format!("{:0>width$}", text, width = decimals + 1);
    let split = padded.len() - decimals;
    let whole = &padded[..split];
    let frac = padded[split..].trim_end_matches('0');
    if frac.is_empty() {
        whole.to_string()
    } else {
        format!("{whole}.{frac}")
    }
}

fn rate(amount_in: &str, out: u128, decimals: u32) -> Value {
    let sold: f64 = amount_in.parse().unwrap_or(0.0);
    if sold <= 0.0 {
        return Value::Null;
    }
    let got: f64 = from_base_units(out, decimals).parse().unwrap_or(0.0);
    json!(round9(got / sold))
}

fn round9(value: f64) -> f64 {
    (value * 1e9).round() / 1e9
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base_units_survive_18_decimals() {
        assert_eq!(to_base_units("1.5", 6).unwrap(), 1_500_000);
        assert_eq!(to_base_units("1", 18).unwrap(), 1_000_000_000_000_000_000);
        assert_eq!(
            to_base_units("1234.000000000000000001", 18).unwrap(),
            1_234_000_000_000_000_000_001
        );
        assert_eq!(from_base_units(1_500_000, 6), "1.5");
        assert_eq!(from_base_units(1_000_000_000_000_000_000, 18), "1");
        assert_eq!(from_base_units(1, 18), "0.000000000000000001");
    }

    #[test]
    fn precision_beyond_the_token_is_refused_not_rounded() {
        assert!(to_base_units("1.0000001", 6).is_err());
        assert!(to_base_units("0.0000001", 6).is_err());
        assert!(to_base_units("-1", 18).is_err());
        assert_eq!(to_base_units("1.5000000", 6).unwrap(), 1_500_000);
    }

    #[test]
    fn big_uints_keep_every_digit() {
        // The eth module stringifies anything past 2^53 precisely so this does
        // not go through an f64 — a wei amount that rounds is a wrong trade.
        let huge = serde_json::json!([u128::MAX.to_string()]);
        assert_eq!(first_uint(Some(&huge)), Some(u128::MAX));
        let small: Value = serde_json::from_str("[1500000]").unwrap();
        assert_eq!(first_uint(Some(&small)), Some(1_500_000));
        let hex = serde_json::json!("0x1f4");
        assert_eq!(first_uint(Some(&hex)), Some(500));
    }

    #[test]
    fn tao_pairs_read_as_stake_and_unstake() {
        assert!(matches!(tao_side("TAO", "SN64"), Ok((64, TaoSide::Buy))));
        assert!(matches!(tao_side("sn8", "tao"), Ok((8, TaoSide::Sell))));
        assert!(matches!(tao_side("TAO", "277"), Ok((277, TaoSide::Buy))));
        assert!(tao_side("SN8", "SN64").is_err());
        assert!(tao_side("TAO", "TAO").is_err());
    }

    #[test]
    fn v3_paths_pack_addresses_and_fees() {
        let path = v3_path(
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
            &[500, 3000],
        )
        .unwrap();
        assert_eq!(path.len(), 2 + 40 + 6 + 40 + 6 + 40);
        assert!(path.contains("0001f4"));
        assert!(path.contains("000bb8"));
    }

    #[test]
    fn chain_aliases_land_on_one_venue() {
        assert_eq!(chain("eth").unwrap().id, "ethereum");
        assert_eq!(chain("Bittensor").unwrap().id, "tao");
        assert_eq!(chain(" BASE ").unwrap().id, "base");
        assert!(chain("dogecoin").is_none());
    }

    #[test]
    fn tool_errors_come_back_as_errors() {
        let response = serde_json::json!({
            "result": { "content": [{ "type": "text", "text": "{\"error\":\"locked\"}" }],
                        "isError": true }
        });
        assert_eq!(
            unwrap_tool_result(response, "eth", "eth_write").unwrap_err(),
            "eth.eth_write: locked"
        );
    }

    #[test]
    fn sse_framed_answers_parse() {
        let body = "event: message\ndata: {\"result\":{\"content\":[{\"text\":\"{}\"}]}}\n\n";
        assert!(parse_body(body).is_some());
    }
}
