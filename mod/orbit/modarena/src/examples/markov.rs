// A generative model with no floating point in sight: an order-2 character
// Markov chain over a corpus baked into the module.
//
// The counts are built at instantiation from the text below rather than
// shipped as a table, which keeps the module small and makes the point that a
// "model" in this registry is whatever a module says it is. Same story as
// mlp.wasm: seed in, sample out, runs in a tab.

include!("abi.rs");

use std::collections::HashMap;

/// The training data. Small enough to read, which is more than can be said
/// for most corpora.
const CORPUS: &str = "\
a module is stored by the hash of its bytes. a game is a module that exports \
the game abi. a player is a module that exports play. the arena stores the \
module, the browser runs the module, and the leaderboard remembers what \
happened. anything wasm can be stored. anything stored can be run. a match is \
its seed and its moves, so a match can be replayed. the registry does not \
decide what a module is for; the exports do.";

const ORDER: usize = 2;

/// A tiny xorshift, so the same seed gives the same text on every engine.
struct Rng(u32);

impl Rng {
    fn next(&mut self) -> u32 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 17;
        self.0 ^= self.0 << 5;
        self.0
    }
}

fn table() -> HashMap<String, Vec<char>> {
    let chars: Vec<char> = CORPUS.chars().collect();
    let mut t: HashMap<String, Vec<char>> = HashMap::new();
    for i in 0..chars.len().saturating_sub(ORDER) {
        let ctx: String = chars[i..i + ORDER].iter().collect();
        t.entry(ctx).or_default().push(chars[i + ORDER]);
    }
    t
}

/// `n` characters of new text from `seed`. Falls back to restarting the chain
/// whenever it walks into a context the corpus never saw.
#[no_mangle]
pub extern "C" fn generate(seed: i32, n: i32) -> i64 {
    let t = table();
    let mut rng = Rng((seed as u32) | 1);
    let want = n.clamp(1, 4000) as usize;

    let mut ctx: String = CORPUS.chars().take(ORDER).collect();
    let mut out = ctx.clone();
    while out.chars().count() < want {
        let next = match t.get(&ctx) {
            Some(options) if !options.is_empty() => {
                options[(rng.next() as usize) % options.len()]
            }
            _ => {
                ctx = CORPUS.chars().take(ORDER).collect();
                continue;
            }
        };
        out.push(next);
        ctx = out.chars().skip(out.chars().count() - ORDER).collect();
    }
    ret(out)
}

/// What the model is, without running it.
#[no_mangle]
pub extern "C" fn describe() -> i64 {
    let t = table();
    ret(format!(
        "{{\"kind\":\"markov\",\"order\":{ORDER},\"corpus_chars\":{},\"contexts\":{},\
          \"entry\":\"generate(seed: i32, n: i32) -> text\"}}",
        CORPUS.chars().count(),
        t.len()
    ))
}
