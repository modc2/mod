//! The capability layer — everything the arena can do, once.
//!
//! `mcp.rs` exposes these as MCP tools, `http.rs` exposes the same ones as
//! REST, and `mod.py` calls them over that. Nothing is implemented twice, so
//! an agent and a browser can never drift apart on what the arena does.

use crate::blobs;
use crate::players;
use crate::rating;
use crate::store::{self, Match, Player, Rating, Seat, Turn, WasmModule};
use crate::wasm;
use serde_json::{json, Value};

/// Where the example pack lives. Baked as a path, not as bytes, so the pack
/// can be rebuilt without rebuilding the server.
fn examples_dir() -> std::path::PathBuf {
    if let Ok(d) = std::env::var("ARENA_EXAMPLES") {
        return std::path::PathBuf::from(d);
    }
    std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../examples/wasm"))
}

pub fn info() -> Value {
    let (modules, games, players_n, matches) = store::read(|s| {
        (
            s.modules.len(),
            s.modules.values().filter(|m| m.role == "game").count(),
            s.players.len(),
            s.matches.len(),
        )
    });
    json!({
        "name": "arena",
        "what": "A wasm storage and execution layer, and an arena built on it: \
                 modules are stored by the hash of their bytes, executed in the browser \
                 (or the node runner), and the ones that implement the game ABI become \
                 games that agents and models are assessed on.",
        "modules": modules,
        "games": games,
        "players": players_n,
        "matches": matches,
        "player_kinds": players::KINDS,
        "executes_in": ["browser", "node"],
        "state": blobs::state_dir().to_string_lossy(),
        "abi": {
            "strings": "the module exports alloc(i32)->i32; anything it returns is one i64 packed as (ptr << 32) | len",
            "game": wasm::GAME_EXPORTS,
            "game_optional": ["game_info", "game_turn", "alloc"],
            "player": wasm::PLAYER_EXPORTS,
        },
    })
}

// ── modules ──────────────────────────────────────────────────────────────

/// Store a module. The id is the hash of the bytes, so uploading the same
/// wasm twice updates the metadata and never duplicates the blob.
pub fn put_module(args: &Value) -> Result<Value, String> {
    let encoded = args
        .get("bytes")
        .or_else(|| args.get("wasm"))
        .or_else(|| args.get("base64"))
        .and_then(|v| v.as_str())
        .ok_or("put_module needs `bytes` — the module, base64 or hex encoded")?;
    let raw = blobs::decode(encoded)?;
    if raw.is_empty() {
        return Err("put_module got zero bytes".into());
    }
    // Parse before storing: a blob that cannot be described is not a module,
    // and the registry promises every entry can be introspected.
    let described = wasm::describe(&raw)?;
    let id = blobs::put(&raw)?;

    let asked = args
        .get("name")
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("module-{}", &id[..8]));

    // Bytes that are already here keep the name they arrived under. The id is
    // the content, so a re-upload carries nothing new to name — and renaming
    // on re-upload would let anyone move a game out from under the players
    // entered at it, just by uploading a copy.
    let renamed = store::read(|s| s.modules.get(&id).map(|m| m.name.clone()))
        .filter(|existing| *existing != asked);

    let module = store::write(|s| {
        let existing = s.modules.get(&id).cloned();
        let m = WasmModule {
            id: id.clone(),
            name: existing.as_ref().map(|e| e.name.clone()).unwrap_or(asked),
            role: described["role"].as_str().unwrap_or("wasm").to_string(),
            description: args.get("description").and_then(|v| v.as_str()).unwrap_or("").into(),
            author: args.get("author").and_then(|v| v.as_str()).unwrap_or("").into(),
            tags: args
                .get("tags")
                .and_then(|v| v.as_array())
                .map(|a| a.iter().filter_map(|t| t.as_str().map(String::from)).collect())
                .unwrap_or_default(),
            size: raw.len(),
            info: described,
            source: args.get("source").and_then(|v| v.as_str()).unwrap_or("upload").into(),
            runs: existing.as_ref().map(|e| e.runs).unwrap_or(0),
            created: existing.as_ref().map(|e| e.created).unwrap_or_else(store::now),
        };
        s.modules.insert(id.clone(), m.clone());
        m
    });

    let mut v = module.card();
    v["url"] = json!(module.url());
    v["info"] = module.info;
    if let Some(kept) = renamed {
        v["note"] = json!(format!(
            "these bytes were already stored as `{kept}` — the id is the content, so the name stands"
        ));
    }
    Ok(v)
}

pub fn list_modules(args: &Value) -> Value {
    let role = args.get("role").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    let q = args.get("q").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    let tag = args.get("tag").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();

    let list = store::read(|s| {
        s.module_list()
            .into_iter()
            .filter(|m| role.is_empty() || m.role == role)
            .filter(|m| tag.is_empty() || m.tags.iter().any(|t| t.to_lowercase() == tag))
            .filter(|m| {
                q.is_empty()
                    || m.name.to_lowercase().contains(&q)
                    || m.description.to_lowercase().contains(&q)
                    || m.id.starts_with(&q)
            })
            .map(|m| m.card())
            .collect::<Vec<_>>()
    });
    json!({ "count": list.len(), "modules": list })
}

pub fn get_module(key: &str) -> Result<Value, String> {
    let m = store::read(|s| s.module(key).cloned())
        .ok_or_else(|| format!("no module `{key}` — ids resolve in full, by name, or by an unambiguous prefix of {}+ hex characters", blobs::MIN_PREFIX))?;
    let mut v = m.card();
    v["info"] = m.info.clone();
    v["url"] = json!(m.url());
    v["stored"] = json!(blobs::exists(&m.id));
    Ok(v)
}

pub fn module_bytes(key: &str) -> Result<(String, Vec<u8>), String> {
    let id = store::read(|s| s.module(key).map(|m| m.id.clone()))
        .ok_or_else(|| format!("no module `{key}`"))?;
    let bytes = blobs::get(&id)?;
    Ok((id, bytes))
}

pub fn delete_module(key: &str) -> Result<Value, String> {
    let m = store::read(|s| s.module(key).cloned()).ok_or_else(|| format!("no module `{key}`"))?;
    let players_using = store::read(|s| {
        s.players
            .values()
            .filter(|p| {
                // Resolve the player's module key rather than string-matching
                // it: a player entered by name would otherwise slip past this
                // and lose its module mid-match.
                p.config
                    .get("module")
                    .and_then(|v| v.as_str())
                    .and_then(|key| s.module(key))
                    .is_some_and(|used| used.id == m.id)
            })
            .map(|p| p.name.clone())
            .collect::<Vec<_>>()
    });
    if !players_using.is_empty() {
        return Err(format!(
            "module {} is what {} plays with — remove the player first",
            m.short(),
            players_using.join(", ")
        ));
    }
    store::write(|s| s.modules.remove(&m.id));
    blobs::remove(&m.id);
    Ok(json!({ "removed": m.id, "name": m.name }))
}

/// Describe bytes without storing them — how the console previews a file the
/// moment it is dropped, before anyone commits to keeping it.
pub fn inspect(args: &Value) -> Result<Value, String> {
    let encoded = args
        .get("bytes")
        .or_else(|| args.get("wasm"))
        .and_then(|v| v.as_str())
        .ok_or("inspect needs `bytes`")?;
    let raw = blobs::decode(encoded)?;
    let mut v = wasm::describe(&raw)?;
    v["id"] = json!(blobs::hash(&raw));
    v["stored"] = json!(blobs::exists(&blobs::hash(&raw)));
    Ok(v)
}

/// Plant the example pack. Called once at startup, and by the `examples` tool
/// when someone rebuilds it. Idempotent — the ids are the content.
pub fn plant_examples() -> Value {
    let dir = examples_dir();
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return json!({ "planted": 0, "dir": dir.to_string_lossy(),
                       "note": "no example pack on disk — run src/examples/build.sh to compile it" });
    };
    let mut planted = Vec::new();
    let mut failed = Vec::new();
    let mut files: Vec<_> = entries.filter_map(|e| e.ok()).map(|e| e.path()).collect();
    files.sort();

    for path in files {
        if path.extension().and_then(|e| e.to_str()) != Some("wasm") {
            continue;
        }
        let Ok(raw) = std::fs::read(&path) else { continue };
        let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("example").to_string();
        // An optional sidecar gives the example its name and description.
        let meta: Value = std::fs::read_to_string(path.with_extension("json"))
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_else(|| json!({}));

        let args = json!({
            "bytes": blobs::to_base64(&raw),
            "name": meta.get("name").and_then(|v| v.as_str()).unwrap_or(&stem),
            "description": meta.get("description").and_then(|v| v.as_str()).unwrap_or(""),
            "author": meta.get("author").and_then(|v| v.as_str()).unwrap_or("arena"),
            "tags": meta.get("tags").cloned().unwrap_or_else(|| json!(["example"])),
            "source": "example",
        });
        match put_module(&args) {
            Ok(v) => planted.push(json!({
                "name": v["name"], "role": v["role"], "id": v["id"], "size": v["size"],
            })),
            Err(e) => failed.push(json!({ "file": stem, "error": e })),
        }
    }
    json!({ "planted": planted.len(), "modules": planted, "failed": failed,
            "dir": dir.to_string_lossy() })
}

// ── players ──────────────────────────────────────────────────────────────

pub fn enter_player(args: &Value) -> Result<Value, String> {
    let name = args
        .get("name")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or("enter_player needs `name`")?
        .to_string();
    let kind = args
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("model")
        .trim()
        .to_lowercase();
    if !players::KINDS.contains(&kind.as_str()) {
        return Err(format!("unknown kind `{kind}` — expected one of {:?}", players::KINDS));
    }
    let mut config = args.get("config").cloned().unwrap_or_else(|| json!({}));

    // Fail here rather than three turns into a match.
    match kind.as_str() {
        "wasm" => {
            let module = config
                .get("module")
                .and_then(|v| v.as_str())
                .ok_or("a wasm player needs config.module — the id of a module that exports `play`")?
                .to_string();
            let m = store::read(|s| s.module(&module).cloned())
                .ok_or_else(|| format!("no module `{module}`"))?;
            if m.role != "player" {
                return Err(format!(
                    "module {} is a `{}`, not a player — a player module must export `play`",
                    m.short(),
                    m.role
                ));
            }
            // Pin the resolution now. A player entered by name would otherwise
            // follow that name if it ever moved to different bytes.
            config["module"] = json!(m.id);
        }
        "model" => {
            config
                .get("model")
                .and_then(|v| v.as_str())
                .ok_or("a model player needs config.model, e.g. \"anthropic/claude-opus-5\"")?;
        }
        "http" => {
            config.get("url").and_then(|v| v.as_str()).ok_or("an http player needs config.url")?;
        }
        _ => {}
    }

    let player = store::write(|s| {
        // Re-entering a name updates it in place, keeping its record — an
        // owner fixing a typo in a config should not lose a rating.
        let existing = s.players.values().find(|p| p.name.eq_ignore_ascii_case(&name)).cloned();
        let mut p = match existing {
            Some(p) => p,
            None => {
                let id = s.next("p");
                Player {
                    id,
                    name: name.clone(),
                    kind: kind.clone(),
                    owner: String::new(),
                    note: String::new(),
                    config: json!({}),
                    overall: Rating::default(),
                    by_game: Default::default(),
                    moves: 0,
                    illegal: 0,
                    timeouts: 0,
                    move_ms_sum: 0,
                    created: store::now(),
                }
            }
        };
        p.kind = kind.clone();
        p.config = config.clone();
        if let Some(v) = args.get("owner").and_then(|v| v.as_str()) {
            p.owner = v.to_string();
        }
        if let Some(v) = args.get("note").and_then(|v| v.as_str()) {
            p.note = v.to_string();
        }
        s.players.insert(p.id.clone(), p.clone());
        p
    });
    Ok(player.card())
}

/// A player's config with its secrets taken out. Anyone can read a player —
/// the console does it on load — and a config holds whatever its driver needs,
/// which for a model is an API key.
fn redact(config: &Value) -> Value {
    let Some(obj) = config.as_object() else {
        return config.clone();
    };
    obj.iter()
        .map(|(k, v)| {
            let lower = k.to_lowercase();
            let secret = ["key", "token", "secret", "password", "authorization", "headers"]
                .iter()
                .any(|s| lower.contains(s));
            let shown = if secret && !v.is_null() { json!("···") } else { v.clone() };
            (k.clone(), shown)
        })
        .collect::<serde_json::Map<_, _>>()
        .into()
}

pub fn list_players(args: &Value) -> Value {
    let kind = args.get("kind").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    let list = store::read(|s| {
        s.player_list()
            .into_iter()
            .filter(|p| kind.is_empty() || p.kind == kind)
            .map(|p| p.card())
            .collect::<Vec<_>>()
    });
    json!({ "count": list.len(), "players": list })
}

pub fn get_player(key: &str) -> Result<Value, String> {
    let p = store::read(|s| s.player(key).cloned()).ok_or_else(|| format!("no player `{key}`"))?;
    let names = store::read(|s| {
        p.by_game
            .keys()
            .map(|g| {
                let name = s.modules.get(g).map(|m| m.name.clone()).unwrap_or_else(|| g[..8.min(g.len())].into());
                (g.clone(), name)
            })
            .collect::<Vec<_>>()
    });
    let mut card = p.card();
    card["config"] = redact(&p.config);
    card["by_game"] = json!(names
        .iter()
        .map(|(id, name)| {
            let mut v = p.by_game.get(id).cloned().unwrap_or_default().card();
            v["game"] = json!(id);
            v["game_name"] = json!(name);
            v
        })
        .collect::<Vec<_>>());
    Ok(card)
}

pub fn remove_player(key: &str) -> Result<Value, String> {
    let p = store::read(|s| s.player(key).cloned()).ok_or_else(|| format!("no player `{key}`"))?;
    store::write(|s| s.players.remove(&p.id));
    Ok(json!({ "removed": p.id, "name": p.name }))
}

/// One move from a player the execution layer cannot drive itself. This is the
/// only outbound call the server makes on a match's behalf.
pub async fn play(key: &str, view: &str, seat: usize) -> Result<Value, String> {
    let p = store::read(|s| s.player(key).cloned()).ok_or_else(|| format!("no player `{key}`"))?;
    let t0 = std::time::Instant::now();
    let a = players::play(&p, view, seat).await?;
    Ok(json!({
        "player": p.name, "seat": seat, "move": a.mv, "raw": a.raw, "note": a.note,
        "ms": t0.elapsed().as_millis() as u64, "meta": a.meta,
    }))
}

// ── matches ──────────────────────────────────────────────────────────────

/// Take a finished match from a runner, rate it, and keep it.
///
/// The runner is trusted for the outcome — it is the thing that actually ran
/// the wasm. What makes that honest rather than hopeful is the transcript:
/// the seed and every move are recorded, the game module is pure over its
/// state, so anyone can replay a match and get the same scores. A leaderboard
/// here is a claim with its working attached.
fn bump(r: &mut Rating, score: f64, result: &str, delta: f64) {
    r.matches += 1;
    r.score_sum += score;
    match result {
        "win" => r.wins += 1,
        "draw" => r.draws += 1,
        _ => r.losses += 1,
    }
    r.elo += delta;
}

pub fn record_match(rec: &Value) -> Result<Value, String> {
    let game_key = rec
        .get("game")
        .and_then(|v| v.as_str())
        .ok_or("a match record needs `game`")?;
    let game = store::read(|s| s.module(game_key).cloned())
        .ok_or_else(|| format!("no game module `{game_key}`"))?;

    let seats_in = rec
        .get("seats")
        .and_then(|v| v.as_array())
        .ok_or("a match record needs `seats`")?;
    if seats_in.is_empty() {
        return Err("a match record needs at least one seat".into());
    }

    // Resolve every seat to a real player before touching any rating.
    let mut resolved: Vec<(Player, Value)> = Vec::new();
    for s in seats_in {
        let key = s
            .get("player_id")
            .or_else(|| s.get("player"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let p = store::read(|st| st.player(key).cloned())
            .ok_or_else(|| format!("no player `{key}` in seat {}", resolved.len()))?;
        resolved.push((p, s.clone()));
    }

    let scores: Vec<f64> = resolved
        .iter()
        .map(|(_, s)| s.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0))
        .collect();
    // Two ratings move, and they are rated against different fields: the
    // per-game one against how these players do at *this* game, the overall
    // one against how they do in general. Using the per-game delta for both
    // would let a specialist's first match against a strong all-rounder count
    // twice at the wrong odds.
    let elos: Vec<f64> = resolved
        .iter()
        .map(|(p, _)| p.by_game.get(&game.id).map(|r| r.elo).unwrap_or(store::START_ELO))
        .collect();
    let overall_elos: Vec<f64> = resolved.iter().map(|(p, _)| p.overall.elo).collect();
    let rated = resolved.len() >= 2;
    let deltas = if rated { rating::deltas(&elos, &scores) } else { vec![0.0; elos.len()] };
    let overall_deltas = if rated {
        rating::deltas(&overall_elos, &scores)
    } else {
        vec![0.0; elos.len()]
    };

    let turns: Vec<Turn> = rec
        .get("turns")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|t| serde_json::from_value(t.clone()).ok()).collect())
        .unwrap_or_default();

    let out = store::write(|st| {
        let mut seats: Vec<Seat> = Vec::new();
        for (i, (p, raw)) in resolved.iter().enumerate() {
            let moves = raw.get("moves").and_then(|v| v.as_u64()).unwrap_or(0);
            let illegal = raw.get("illegal").and_then(|v| v.as_u64()).unwrap_or(0);
            let timeouts = raw.get("timeouts").and_then(|v| v.as_u64()).unwrap_or(0);
            let ms = raw.get("ms").and_then(|v| v.as_u64()).unwrap_or(0);
            let result = rating::outcome(scores[i], &scores);

            if let Some(pl) = st.players.get_mut(&p.id) {
                pl.moves += moves;
                pl.illegal += illegal;
                pl.timeouts += timeouts;
                pl.move_ms_sum += ms;
                bump(&mut pl.overall, scores[i], result, overall_deltas[i]);
                bump(pl.by_game.entry(game.id.clone()).or_default(), scores[i], result, deltas[i]);
            }

            seats.push(Seat {
                seat: i,
                player_id: p.id.clone(),
                player_name: p.name.clone(),
                score: scores[i],
                moves,
                illegal,
                timeouts,
                ms,
                elo_before: elos[i],
                elo_after: elos[i] + deltas[i],
                error: raw.get("error").and_then(|v| v.as_str()).unwrap_or("").into(),
            });
        }

        if let Some(m) = st.modules.get_mut(&game.id) {
            m.runs += 1;
        }

        let id = st.next("m");
        let m = Match {
            id,
            game: game.id.clone(),
            game_name: rec.get("game_name").and_then(|v| v.as_str()).unwrap_or(&game.name).into(),
            seed: rec.get("seed").and_then(|v| v.as_i64()).unwrap_or(0),
            seats,
            turns,
            summary: rec.get("summary").and_then(|v| v.as_str()).unwrap_or("").into(),
            runtime: rec.get("runtime").and_then(|v| v.as_str()).unwrap_or("unknown").into(),
            rated,
            ms: rec.get("ms").and_then(|v| v.as_u64()).unwrap_or(0),
            created: store::now(),
        };
        st.record_match(m.clone());
        m
    });

    let mut v = out.brief();
    v["rated"] = json!(rated);
    if !rated {
        v["note"] = json!("one seat — practice, so no rating moved");
    }
    Ok(v)
}

pub fn list_matches(args: &Value) -> Value {
    let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(20).clamp(1, 200) as usize;
    let game = args.get("game").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let list = store::read(|s| {
        let gid = if game.is_empty() { None } else { s.module(&game).map(|m| m.id.clone()) };
        s.matches
            .iter()
            .rev()
            .filter(|m| gid.as_ref().map(|g| &m.game == g).unwrap_or(true))
            .take(limit)
            .map(|m| m.brief())
            .collect::<Vec<_>>()
    });
    json!({ "count": list.len(), "matches": list })
}

pub fn get_match(id: &str) -> Result<Value, String> {
    store::read(|s| s.matches.iter().find(|m| m.id == id).cloned())
        .map(|m| serde_json::to_value(&m).unwrap_or_else(|_| json!({})))
        .ok_or_else(|| format!("no match `{id}`"))
}

/// Players ranked. Per game when one is named — which is the ranking that
/// means something, since being good at nim says nothing about poker.
pub fn leaderboard(args: &Value) -> Result<Value, String> {
    let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(20).clamp(1, 200) as usize;
    let game = args.get("game").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();

    if game.is_empty() {
        let rows = store::read(|s| {
            s.player_list()
                .into_iter()
                .filter(|p| p.overall.matches > 0)
                .take(limit)
                .map(|p| p.card())
                .collect::<Vec<_>>()
        });
        return Ok(json!({ "scope": "overall", "count": rows.len(), "players": rows }));
    }

    let m = store::read(|s| s.module(&game).cloned()).ok_or_else(|| format!("no game `{game}`"))?;
    let mut rows = store::read(|s| {
        s.players
            .values()
            .filter_map(|p| {
                let r = p.by_game.get(&m.id)?;
                let mut v = r.card();
                v["id"] = json!(p.id);
                v["name"] = json!(p.name);
                v["kind"] = json!(p.kind);
                Some((r.elo, v))
            })
            .collect::<Vec<_>>()
    });
    rows.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    Ok(json!({
        "scope": m.name, "game": m.id, "count": rows.len().min(limit),
        "players": rows.into_iter().take(limit).map(|(_, v)| v).collect::<Vec<_>>(),
    }))
}
