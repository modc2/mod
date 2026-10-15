"""lessons.py — the LEARN track: NEAR smart contracts for complete beginners,
then the zero-knowledge ladder, then how the cairo mod carries you up it.

Plain data, no logic. Served by mod.py `lessons()/lesson(id)`, the console's
LEARN tab, the MCP tools near_lessons / near_lesson, and the tutor agent
(which quotes a lesson back when a question touches its topics).

A lesson is {id, title, level, minutes, summary, topics, sections, try?}.
A section is {h, say, code?, lang?, table?, tip?, warn?}: `say` is plain
English (blank line = new paragraph), `code` a snippet, `table` rows of
strings (first row = header). `try` names a starter the BUILD tab scaffolds
in one click and what to do with it afterwards. `cairo` on a lesson points
at the twin lesson/starter in orbit/cairo.
"""

LESSONS = [
    {
        'id': 'an-account-is-a-contract',
        'title': 'On NEAR, an account IS a contract',
        'level': 'start here',
        'minutes': 4,
        'summary': 'Accounts have names like alice.near, and putting code on one turns it into a contract. The one idea everything else hangs off.',
        'topics': ['account', 'contract', 'what is', 'near', 'deploy', 'name', 'implicit', 'storage', 'gas'],
        'sections': [
            {'h': 'A vending machine you can\'t argue with',
             'say': 'A smart contract is a small program that lives on a blockchain. Like a vending machine: you press a button, it does exactly what it was built to do, and nobody (not even the person who built it) can reach in and bend the rules afterwards.\n\n'
                    'On most chains a contract gets a random hex address. On NEAR it gets a name. `wrap.near`, `usdt.tether-token.near`, `yourname.testnet`: those are accounts, and an account with code on it is a contract. Same object, same name.'},
            {'h': 'What that means in practice',
             'table': [
                 ['you want to', 'on NEAR that is'],
                 ['make a contract', 'make an account, then put code on it (deploy)'],
                 ['upgrade it', 'deploy again to the same account; the data stays'],
                 ['know who called', 'the caller\'s account name, e.g. alice.testnet'],
                 ['give an app limited access', 'add a FunctionCall key that may only call that contract'],
                 ['pay for the data it keeps', 'the account locks 1 NEAR per 100 KB of storage (staked, not burned)'],
             ]},
            {'h': 'Two kinds of names',
             'say': 'Named accounts are made by a parent: `*.near` by the registrar, `*.testnet` by the testnet faucet, and `app.alice.near` by alice herself (sub-accounts). Implicit accounts are 64 hex characters, which is just a public key: anyone can send to one before it exists.\n\n'
                    'In this workshop you will use testnet: free accounts, free NEAR, same rules as mainnet.',
             'tip': 'The WALLET tab makes a *.testnet account in one click; the faucet funds it with about 10 N. That account is where your first contract will live.'},
            {'h': 'Gas, in one breath',
             'say': 'Every change you make costs gas, paid in NEAR by whoever sends the transaction. Reading is free. A call may use up to 300 Tgas (tera-gas), and a simple method uses a few. Gas you attach but do not use is refunded.\n\n'
                    'You rarely think about gas on NEAR; the SDK and the console pick sane defaults. You will think about storage: every byte a contract keeps is NEAR the account must hold.'},
        ],
        'try': {'template': 'counter',
                'steps': ['Open BUILD and make a counter.', 'Press Build, then Test.', 'Press Ship: it lands on your testnet account.', 'CALL tab: VIEW get, CHANGE increment, VIEW get again.']},
    },
    {
        'id': 'anatomy-of-a-contract',
        'title': 'The four parts of every contract',
        'level': 'start here',
        'minutes': 5,
        'summary': 'Storage, views, changes and events. Read a whole NEAR contract in Rust without knowing Rust.',
        'topics': ['anatomy', 'storage', 'view', 'change', 'event', 'rust', 'struct', 'near-sdk', 'sdk', 'macro', 'borsh', 'log'],
        'cairo': 'smart-contracts-101',
        'sections': [
            {'h': 'The same four parts, every time',
             'table': [
                 ['part', 'vending-machine version', 'in Rust (near-sdk)'],
                 ['storage', 'how many snacks are inside', 'the struct marked #[near(contract_state)]'],
                 ['view', 'looking through the glass', 'a method taking &self (free, no transaction)'],
                 ['change', 'pressing a button', 'a method taking &mut self (signed, costs gas)'],
                 ['event', 'the printed receipt', 'a log line starting with EVENT_JSON:'],
             ]},
            {'h': 'A whole contract',
             'say': 'This is the counter starter, trimmed. Do not worry about every word; find the four parts.',
             'lang': 'rust',
             'code': '#[near(contract_state)]            // storage: lives under the account\n'
                     '#[derive(PanicOnDefault)]\n'
                     'pub struct Counter { value: i64, owner: AccountId }\n\n'
                     '#[near]\n'
                     'impl Counter {\n'
                     '    #[init]                                   // runs once, at deploy\n'
                     '    pub fn new() -> Self {\n'
                     '        Self { value: 0, owner: env::predecessor_account_id() }\n'
                     '    }\n'
                     '    pub fn get(&self) -> i64 { self.value }     // view: just looks\n'
                     '    pub fn increment(&mut self) -> i64 {         // change: presses a button\n'
                     '        self.value += 1;\n'
                     '        env::log_str("EVENT_JSON:{\\"event\\":\\"increment\\"}"); // event\n'
                     '        self.value\n'
                     '    }\n'
                     '    pub fn reset(&mut self) {\n'
                     '        require!(env::predecessor_account_id() == self.owner, "only the owner can reset");\n'
                     '        self.value = 0;\n'
                     '    }\n'
                     '}'},
            {'h': 'How the SDK hides the plumbing',
             'say': '`#[near]` is a macro: it writes the boring code for you. Before your method runs it loads the struct from storage (borsh bytes under the account) and parses the JSON arguments; after a `&mut self` method it writes the struct back. You write ordinary Rust in between.\n\n'
                    'NEAR keeps no ABI on chain. That is why the CALL tab reads the method list straight out of your .wasm file\'s export table: every `pub fn` in the `#[near] impl` becomes one export.'},
            {'h': 'Saying no',
             'say': '`require!(condition, "message")` is how a contract refuses. If the condition is false the whole call is undone and the message is what the caller sees. Every rule you care about is a require! somewhere.',
             'tip': 'In the BUILD tab, each starter carries a walkthrough: click a note and the editor jumps to the line it explains.'},
        ],
        'try': {'template': 'counter', 'steps': ['Make a counter.', 'Read the walkthrough notes in CODE.', 'Change `+= 1` to `+= 2`, Build, Test: one test now fails. Read why, then fix the test.']},
    },
    {
        'id': 'who-is-calling',
        'title': 'Who is calling? Keys, sessions and the one-yocto trick',
        'level': 'start here',
        'minutes': 5,
        'summary': 'predecessor vs signer, FullAccess vs FunctionCall keys, and why a token transfer asks for one yoctoNEAR.',
        'topics': ['caller', 'predecessor', 'signer', 'access key', 'keys', 'function call key', 'full access', 'session', 'yocto', 'deposit', 'owner', 'auth', 'login'],
        'sections': [
            {'h': 'Three names in every call',
             'table': [
                 ['env::...', 'meaning', 'use it for'],
                 ['predecessor_account_id()', 'the account that called THIS contract', 'ownership checks, balances: almost always this one'],
                 ['signer_account_id()', 'the human who signed the whole transaction', 'rarely; if A calls B calls you, signer is still A'],
                 ['current_account_id()', 'this contract\'s own account', 'building seals/keys that must be unique to this deployment'],
             ]},
            {'h': 'Access keys are sessions',
             'say': 'A NEAR account can hold many keys. A FullAccess key can do anything, including deploy and transfer. A FunctionCall key is limited: one contract, optionally a list of methods, and a gas allowance. It cannot attach NEAR to a call.\n\n'
                    'When you "sign in" to a NEAR app, the wallet adds a FunctionCall key for that app\'s contract to your account. The app can then call its own methods for you without asking every time, and it can never touch your money. Session auth, on chain. The EXPLORE tab lists these keys for any account.'},
            {'h': 'The one-yocto trick',
             'say': 'Because FunctionCall keys cannot attach a deposit, a contract can demand "attach exactly 1 yoctoNEAR" (10^-24 N, worthless) to prove the call came from a FullAccess key, i.e. from a wallet confirmation, not from a logged-in app acting on its own.\n\n'
                    'That is why NEP-141 `ft_transfer` is marked `#[payable]` and starts with `assert_one_yocto()`. The token starter does exactly this.',
             'lang': 'rust',
             'code': '#[payable]\npub fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>) {\n    assert_one_yocto();              // a wallet confirmed this, not a session key\n    let sender = env::predecessor_account_id();\n    ...\n}'},
            {'h': 'Owners are just a stored name',
             'say': 'There is no built-in "owner". The starters save `env::predecessor_account_id()` in `new()` and compare against it later with require!. Change that rule however you like: a list of admins, a vote, a timelock.'},
        ],
    },
    {
        'id': 'build-test-ship',
        'title': 'Build, test, ship: the loop',
        'level': 'start here',
        'minutes': 4,
        'summary': 'What the three buttons do, why tests run without a chain, and how a redeploy upgrades in place.',
        'topics': ['build', 'test', 'ship', 'deploy', 'cargo', 'wasm', 'upgrade', 'init', 'redeploy', 'compile', 'testnet', 'workflow'],
        'sections': [
            {'h': 'Build',
             'say': '`cargo build --target wasm32-unknown-unknown --release` compiles your Rust to WebAssembly, the bytecode NEAR runs. The first build on this box takes about half a minute because near-sdk itself compiles; every build after that takes seconds, since all projects share one target directory.\n\n'
                    'The result is one `.wasm` file, usually 50-200 KB. The BUILD tab shows its size and the exported methods it found, read straight from the binary.'},
            {'h': 'Test',
             'say': 'Unit tests live at the bottom of `src/lib.rs`. `testing_env!` builds a fake NEAR: you decide who the caller is and what they attached, then call your methods like plain Rust functions. A whole suite runs in a second, no chain, no account.',
             'lang': 'rust',
             'code': '#[test]\n#[should_panic(expected = "only the owner can reset")]\nfn stranger_cannot_reset() {\n    as_user("alice.testnet");   // deploys as alice\n    let mut c = Counter::new();\n    as_user("bob.testnet");     // bob calls\n    c.reset();                  // must refuse with that message\n}',
             'tip': 'Test the refusals, not just the happy path. A rule without a should_panic test is a rule nobody checked.'},
            {'h': 'Ship',
             'say': 'Ship hands the .wasm to this module\'s keystore (the same one the WALLET and DEPLOY tabs use). On testnet it just goes; on mainnet it refuses unless you tick the confirm box. The constructor from the starter (`new` + example args) is batched into the same transaction, so the contract is initialised the moment it exists.\n\n'
                    'Deploying again to the same account upgrades the code and keeps the data. The workshop skips the init call on a redeploy, because calling `new()` twice would fail: state already exists.',
             'warn': 'If you change the shape of the storage struct between deploys, the old bytes may no longer parse. Add fields carefully (Option<T> is your friend) or write a migrate method.'},
            {'h': 'Then talk to it',
             'say': 'The CALL tab: VIEW mode for `&self` methods (free), CHANGE mode for `&mut self` methods (signed by the keystore). The TX tab decodes the receipt and shows your EVENT_JSON lines. The EXPLORE tab shows your account with a green "code deployed" dot.'},
        ],
        'try': {'template': 'counter', 'steps': ['Build, Test, Ship.', 'CALL: increment twice, get.', 'Edit the message in reset, Build, Ship again: a redeploy, the count survives.']},
    },
    {
        'id': 'make-your-own-token',
        'title': 'Make your own token (NEP-141 by hand)',
        'level': 'next',
        'minutes': 6,
        'summary': 'Balances in a map, U128 on the wire, owner-only mint, and why the standard matters.',
        'topics': ['token', 'nep-141', 'nep141', 'ft', 'fungible', 'coin', 'mint', 'burn', 'transfer', 'balance', 'u128', 'lookupmap', 'map', 'metadata', 'stablecoin'],
        'cairo': 'build-a-cash-dollar',
        'sections': [
            {'h': 'A token is a spreadsheet with rules',
             'say': 'One column: account, one column: balance. `mint` adds a row, `ft_transfer` moves numbers between rows, `ft_balance_of` reads one. That is the whole thing. The standard (NEP-141) only fixes the method names and shapes so wallets and exchanges can talk to any token the same way.'},
            {'h': 'The three NEAR-specific bits',
             'table': [
                 ['thing', 'why'],
                 ['LookupMap<AccountId, u128>', 'a map in NEAR storage: each balance is its own slot, so reading one never loads all. The b"b" prefix keeps its keys apart from other collections'],
                 ['U128 in public signatures', 'JSON cannot carry a 128-bit integer safely, so amounts travel as strings; U128 converts'],
                 ['#[payable] + assert_one_yocto()', 'the transfer needs a wallet confirmation, not a session key (see "Who is calling")'],
             ]},
            {'h': 'Your rule: who may mint?',
             'say': 'The standard says nothing about creating coins. The starter lets only the owner mint. Make that your product:',
             'table': [
                 ['token', 'mint rule'],
                 ['cash-backed stablecoin', 'a bank role mints only after reporting reserves (the cairo mod\'s stablecoin lesson builds exactly this)'],
                 ['points / rewards', 'a game contract mints when a level is won'],
                 ['fixed supply', 'mint everything to the treasury in new() and delete mint'],
             ]},
            {'h': 'Storage is the hidden cost',
             'say': 'Each new holder is ~100 bytes of state, and state is NEAR the contract account must hold (1 N per 100 KB). Real tokens make the holder pay that once via NEP-145 `storage_deposit`; the starter lets the contract absorb it to stay readable. If your token could have thousands of holders, add storage_deposit.',
             'tip': 'Decimals are display only. With decimals=6, amount "1000000" shows as 1.000000 in wallets. Nothing in the contract divides.'},
        ],
        'try': {'template': 'token', 'steps': ['Make a token, Build, Test, Ship.', 'CALL: VIEW ft_metadata.', 'CHANGE mint to yourself: {"account_id":"you.testnet","amount":"1000000"}.', 'CHANGE ft_transfer to a friend with deposit 0.000000000000000000000001 N.', 'EXPLORE your account: the token balance shows.']},
    },
    {
        'id': 'cross-contract-calls',
        'title': 'Calling other contracts (and why it is async)',
        'level': 'next',
        'minutes': 5,
        'summary': 'Promises, callbacks and the one rule that keeps you safe: never trust state across a yield.',
        'topics': ['cross-contract', 'promise', 'callback', 'async', 'call another contract', 'compose', 'ext_contract', 'gas attach'],
        'sections': [
            {'h': 'NEAR is sharded, so calls are messages',
             'say': 'When your contract calls another one, that call runs in a later block, possibly on another shard. Your method returns a Promise and finishes. If you want the answer, you schedule a callback: another method on your own contract that runs when the result arrives.\n\n'
                    'That is different from Ethereum, where a call returns inline. It is also why NEAR scales, so it is worth learning once.'},
            {'h': 'The shape',
             'lang': 'rust',
             'code': 'use near_sdk::{ext_contract, Promise, PromiseError, Gas, NearToken};\n\n'
                     '#[ext_contract(ext_ft)]\ntrait FungibleToken {\n    fn ft_balance_of(&self, account_id: AccountId) -> U128;\n}\n\n'
                     '#[near]\nimpl MyContract {\n'
                     '    pub fn check_balance(&self, token: AccountId, who: AccountId) -> Promise {\n'
                     '        ext_ft::ext(token)\n            .with_static_gas(Gas::from_tgas(5))\n            .ft_balance_of(who)\n'
                     '            .then(Self::ext(env::current_account_id())\n                .with_static_gas(Gas::from_tgas(5))\n                .on_balance())\n'
                     '    }\n\n'
                     '    #[private]   // only this contract may call its own callback\n'
                     '    pub fn on_balance(&mut self, #[callback_result] r: Result<U128, PromiseError>) -> U128 {\n'
                     '        r.unwrap_or(U128(0))\n'
                     '    }\n}'},
            {'h': 'The one rule',
             'say': 'Between your call and your callback, other transactions run. Anything you read before the call may be stale by the time the callback runs, and if you changed state optimistically you must undo it in the callback when the result is an error. Mark every callback `#[private]` or anyone can call it with fake results.',
             'warn': 'Attach enough gas to the callback. A callback that runs out of gas cannot roll anything back.'},
        ],
    },
    {
        'id': 'zk-ladder',
        'title': 'Zero knowledge, one rung at a time',
        'level': 'zk',
        'minutes': 6,
        'summary': 'From a hash commitment to a full ZK proof: what each rung proves, what it hides, and which rungs NEAR can verify today.',
        'topics': ['zk', 'zero knowledge', 'zero-knowledge', 'proof', 'snark', 'stark', 'private', 'privacy', 'anonymous', 'hide', 'secret', 'commitment', 'verifier', 'groth16', 'alt_bn128', 'bn254', 'ladder'],
        'cairo': 'smart-contracts-101',
        'sections': [
            {'h': 'What "zero knowledge" means',
             'say': 'A proof that something is true which reveals nothing else. "I know the password" without saying it. "I am on the list" without saying who. "This vote is valid" without showing the vote.\n\n'
                    'Full ZK proofs are heavy math. But the idea has rungs, and the first ones are a single hash. Climb them in order and the big one stops being magic.'},
            {'h': 'The ladder',
             'table': [
                 ['rung', 'you prove', 'you hide', 'tool', 'on NEAR today?'],
                 ['1 commitment', 'I decided earlier and did not change my mind', 'the value, until you reveal it', 'sha256', 'yes: the commit_reveal starter'],
                 ['2 membership', 'I am on the list', 'the list (one 32-byte root replaces it)', 'merkle tree', 'yes: the merkle_allowlist starter'],
                 ['3 nullifier', 'I claimed once, and you cannot tell which member I am', 'who claimed', 'hash of a secret + ZK proof', 'needs rung 4'],
                 ['4 SNARK verify', 'this whole computation was done right', 'every input you choose', 'Groth16/PLONK verifier', 'yes: alt_bn128 host functions (pairing checks) exist, so a verifier contract is possible'],
                 ['5 proof-native chain', 'every block is proven', 'as above, cheaply, at scale', 'Cairo + STARKs', 'that is Starknet: the cairo mod'],
             ]},
            {'h': 'Where the cairo mod comes in',
             'say': 'Rungs 1 and 2 you can build and ship here, in Rust, on NEAR testnet, in five minutes. Rungs 3 to 5 are what the cairo mod is for: the same starters exist there in Cairo, using Poseidon (a hash designed to be cheap inside a proof), with a prover and a private practice chain, plus zuma and zquorum: anonymous sets with ring signatures and nullifiers.\n\n'
                    'The tutor in the corner knows both. Ask "make a zk vote" and the builder hands it to the cairo mod\'s builder; ask "make a sealed vote" and it builds the NEAR version here.',
             'tip': 'A Cairo/STARK proof cannot be checked cheaply on NEAR, and a NEAR contract cannot run on Starknet. Prototype the ZK logic in cairo, ship commitments on NEAR, or verify a Groth16 proof on NEAR with the alt_bn128 host functions if you need both.'},
        ],
        'try': {'template': 'commit_reveal', 'steps': ['Make a sealed vote (rung 1).', 'Then open LEARN: Merkle proofs (rung 2).', 'Then ask the tutor: "make a zk version in cairo".']},
    },
    {
        'id': 'commit-reveal',
        'title': 'Commit-reveal: the first rung',
        'level': 'zk',
        'minutes': 5,
        'summary': 'Seal a number so nobody can read it or copy it, then prove it later. One sha256, four ingredients.',
        'topics': ['commit', 'reveal', 'commit-reveal', 'seal', 'sealed', 'salt', 'hash', 'sha256', 'vote', 'voting', 'auction', 'lottery', 'guess'],
        'cairo': 'commit_reveal',
        'sections': [
            {'h': 'The problem',
             'say': 'A sealed-bid auction, a secret vote, rock-paper-scissors: everyone must decide BEFORE seeing the others, but on a public chain every transaction is visible the moment it is sent. Publish your bid and the next person simply outbids you by one.'},
            {'h': 'The trick',
             'say': 'Publish a hash of your value instead. A hash cannot be run backwards, so nobody learns the value; but later, when you publish the value, anyone can re-hash it and see it matches. You committed. Four things go into the seal, and each one closes a hole:',
             'table': [
                 ['ingredient', 'hole it closes'],
                 ['the value', 'the thing you are promising'],
                 ['a random salt', 'without it, a seal of a small number is brute-forced in a second (try 0..100)'],
                 ['your account id', 'without it, bob copies alice\'s seal and reveals her value as his'],
                 ['this contract\'s id', 'without it, a seal from one game could be replayed in another'],
             ],
             'lang': 'rust',
             'code': 'fn seal(contract: &AccountId, who: &AccountId, value: u64, salt: &str) -> [u8; 32] {\n    env::sha256_array(format!("{}|{}|{}|{}", contract, who, value, salt).as_bytes())\n}'},
            {'h': 'Two phases, one owner switch',
             'say': 'During COMMIT the contract stores seals only, one per account. The owner flips to REVEAL; now each account sends its value and salt, the contract recomputes the seal and compares. The starter keeps the count and sum, so the result is the average of everyone\'s honest number.\n\n'
                    'Real systems replace the owner switch with block heights (commit until block N, reveal until N+M) so nobody has to be trusted to flip it.',
             'warn': 'Compute your seal on your own machine. The starter exposes seal_of as a view so you can try it from the console, but a view call to a public RPC shows your value to that RPC.'},
        ],
        'try': {'template': 'commit_reveal', 'steps': ['Make it, Build, Test, Ship.', 'VIEW seal_of {"who":"you.testnet","value":7,"salt":"pepper"}.', 'CHANGE commit {"seal":"<hex>"}.', 'Owner: CHANGE open_reveals. Then CHANGE reveal {"value":7,"salt":"pepper"}.', 'Try reveal with 8: refused.']},
    },
    {
        'id': 'merkle-proofs',
        'title': 'Merkle proofs: prove you are on the list',
        'level': 'zk',
        'minutes': 5,
        'summary': 'One 32-byte root stands for a list of thousands. A member proves membership with a handful of hashes.',
        'topics': ['merkle', 'allowlist', 'whitelist', 'airdrop', 'membership', 'root', 'proof', 'nullifier', 'list', 'claim'],
        'cairo': 'merkle_allowlist',
        'sections': [
            {'h': 'The problem',
             'say': 'You want 50,000 accounts to each claim something once. Storing 50,000 names on chain costs real NEAR (storage is staked) and takes a long transaction. You want the list OFF chain and the proof ON chain.'},
            {'h': 'The tree',
             'say': 'Hash every name (that is a leaf). Hash leaves in pairs to get parents, pair the parents, and so on up to one hash: the root. Store only the root (32 bytes).\n\n'
                    'To prove alice is in, give the contract alice\'s sibling at each level (log2 N hashes, about 16 for 50,000 names). The contract hashes its way up; if it lands on the root, alice was in the tree. It never saw the list.',
             'lang': 'rust',
             'code': 'fn verify(root: &[u8; 32], leaf: &[u8; 32], proof: &[String]) -> bool {\n    let mut node = *leaf;\n    for p in proof { node = parent(&node, &unhex(p)); }   // sorted-pair sha256\n    node == *root\n}'},
            {'h': 'Two small decisions that matter',
             'table': [
                 ['decision', 'why the starter does it'],
                 ['leaf = sha256(name), not the raw name', 'a proof element can never be confused with a leaf (second-preimage trick)'],
                 ['parent = sha256(min(a,b) || max(a,b))', 'sorting the pair means a proof needs no left/right flags'],
             ]},
            {'h': 'From here to anonymity',
             'say': 'This proof still names alice: the caller is her account. Rung 3 replaces the name with a nullifier, a hash of a secret only she knows, and a ZK proof that the nullifier belongs to SOME leaf in the tree. Then the contract learns "a member claimed, and this member has not claimed before", nothing more. That needs rung 4 (a SNARK) and is built in Cairo in the zquorum and zuma mods.',
             'tip': '`m near/merkle alice.testnet,bob.testnet,you.testnet` (or the MERKLE helper in BUILD) computes the root and every proof with the exact scheme the starter checks.'},
        ],
        'try': {'template': 'merkle_allowlist', 'steps': ['Make it, Build, Test, Ship (any root for init).', 'MERKLE helper: paste three accounts incl. yours, copy the root and your proof.', 'CHANGE set_root {"root":"..."} as owner.', 'CHANGE claim {"proof":[...]} from your account. Then again: "already claimed".']},
    },
    {
        'id': 'zk-with-cairo',
        'title': 'Using the cairo mod from here',
        'level': 'zk',
        'minutes': 5,
        'summary': 'What Cairo and Starknet are, how the two workbenches map onto each other, and how to hand the builder a ZK job.',
        'topics': ['cairo', 'starknet', 'stark', 'poseidon', 'scarb', 'snforge', 'practice chain', 'devnet', 'zuma', 'zquorum', 'ring signature', 'map', 'translate', 'twin'],
        'cairo': 'smart-contracts-101',
        'sections': [
            {'h': 'Cairo in one paragraph',
             'say': 'Cairo is a language whose every run can be PROVEN: a prover produces a STARK proof that the program ran correctly, and a verifier checks it far more cheaply than re-running it. Starknet is a chain built on that: contracts are written in Cairo, and the whole chain\'s work is proven to Ethereum. So on Starknet, "zero knowledge" is not a feature you add; it is how the floor is built.\n\n'
                    'The cairo mod on this box is a workbench for it: starters, a builder agent, snforge tests and a private practice chain where nothing costs money. Open it at /cairo/.'},
            {'h': 'The same contract, both languages',
             'say': 'Four starters exist on both sides with the same name, so you can read one next to the other:',
             'table': [
                 ['idea', 'NEAR (Rust, here)', 'Cairo (there)'],
                 ['storage', '#[near(contract_state)] struct', '#[storage] struct Storage'],
                 ['a map', 'store::LookupMap<K, V>', 'Map<K, V>'],
                 ['who is calling', 'env::predecessor_account_id()', 'get_caller_address()'],
                 ['refuse', 'require!(cond, "msg")', 'assert(cond, \'msg\')'],
                 ['event', 'log "EVENT_JSON:{...}"', '#[event] enum + self.emit(...)'],
                 ['hash for a seal', 'env::sha256_array', 'poseidon (cheap inside a proof)'],
                 ['tests', 'cargo test + testing_env!', 'snforge + declare/deploy'],
                 ['practice', 'testnet (free, public)', 'local devnet (free, private)'],
             ]},
            {'h': 'Handing the builder a ZK job',
             'say': 'The tutor orb in the corner of this console has a Build pill. If what you ask for is a NEAR contract ("a tip jar", "a sealed vote"), it scaffolds and edits a Rust crate here. If you ask for zero knowledge ("a zk vote", "anonymous members", "in cairo"), it forwards the whole job to the cairo mod\'s builder and gives you a link to the result. Both run the same agent protocol, so the steps stream back into this window either way.',
             'tip': 'Say "zk", "cairo", "anonymous" or "private" and the builder goes to cairo. Say "sealed" or "commit" and it stays here.'},
            {'h': 'What does not cross over',
             'say': 'A Cairo proof cannot be verified cheaply on NEAR, and a NEAR account cannot call a Starknet contract directly. If you need both chains: keep commitments and settlement on NEAR, prove in Cairo, and either trust a relayer or verify a Groth16 proof on NEAR with the alt_bn128 host functions. That verifier is a real project, not a starter; ask the tutor and it will tell you what it involves.'},
        ],
    },
    {
        'id': 'test-like-a-pro',
        'title': 'Test like a pro',
        'level': 'next',
        'minutes': 4,
        'summary': 'Unit tests in the fake runtime, should_panic for every rule, and when to reach for the sandbox.',
        'topics': ['test', 'tests', 'testing', 'fail', 'failed', 'should_panic', 'workspaces', 'sandbox', 'assert', 'debug'],
        'cairo': 'test-like-a-pro',
        'sections': [
            {'h': 'Three kinds of test',
             'table': [
                 ['kind', 'what it is', 'speed', 'in this workshop'],
                 ['unit', 'call methods as Rust functions in a fake runtime (testing_env!)', '1 second', 'the Test button'],
                 ['sandbox', 'near-workspaces spins a local NEAR and deploys the real .wasm', 'minutes', 'add it to dev-dependencies when you need Promises tested'],
                 ['testnet', 'ship and poke it from the CALL tab', 'live', 'the Ship button'],
             ]},
            {'h': 'The fake runtime',
             'lang': 'rust',
             'code': 'fn as_user(who: &str, deposit_yocto: u128) {\n    let ctx = VMContextBuilder::new()\n        .predecessor_account_id(who.parse().unwrap())\n        .attached_deposit(NearToken::from_yoctonear(deposit_yocto))\n        .build();\n    testing_env!(ctx);   // from here on, env::* answers as this caller\n}',
             'say': 'Set the caller, the deposit, the block height, the account balance: whatever the method reads from env. Then call it. Change the caller mid-test to play two people.'},
            {'h': 'Reading a failure',
             'say': 'A red test prints `panicked at ...` followed by the message from the require! that tripped. Find that message in CODE and you have the rule that fired. If the panic was expected, make sure `should_panic(expected = "...")` quotes a piece of the real message; a typo there fails the test for the wrong reason.',
             'tip': 'Ask the tutor "why did my test fail?" while a project is open: it reads the last run and points at the line.'},
        ],
    },
    {
        'id': 'security-checklist',
        'title': 'Before mainnet: the checklist',
        'level': 'next',
        'minutes': 4,
        'summary': 'The seven mistakes that actually lose money on NEAR, each with the one-line fix.',
        'topics': ['security', 'safe', 'audit', 'mainnet', 'overflow', 'reentrancy', 'storage attack', 'griefing', 'migrate', 'checklist', 'bug'],
        'sections': [
            {'h': 'The list',
             'table': [
                 ['mistake', 'fix'],
                 ['a change method with no require! on who may call it', 'every &mut self method: who may call this, and is it checked?'],
                 ['integer overflow', 'keep overflow-checks = true (the starters do); panics beat wrap-around'],
                 ['anyone can call your callback', '#[private] on every callback'],
                 ['state changed before a cross-contract call, never undone on failure', 'change state in the callback, or roll back there'],
                 ['strangers can make you store data for free', 'charge storage (NEP-145) or cap per-caller entries'],
                 ['new struct fields on redeploy, old state will not parse', 'Option<T> for new fields, or a migrate method'],
                 ['a FullAccess key on the contract account after launch', 'delete it: then the code can never be changed, which is often the point'],
             ]},
            {'h': 'And the boring one',
             'say': 'Write the should_panic test for every rule in the table. If you cannot write the test, you do not have the rule.',
             'warn': 'Nothing in this workshop is a security review. Testnet is free; mainnet is not. Get eyes on the code before real NEAR goes in.'},
        ],
    },
]

GLOSSARY = {
    'account': 'a named identity on NEAR (alice.near) or 64 hex chars; with code on it, it is also a contract',
    'contract': 'a program stored on an account; its methods are what the account can be asked to do',
    'deploy': 'putting compiled code (.wasm) on your own account; deploying again upgrades in place',
    'wasm': 'WebAssembly, the bytecode NEAR runs; cargo compiles your Rust into one .wasm file',
    'view': 'a method taking &self: reads only, free, needs no key or transaction',
    'change': 'a method taking &mut self: can write storage, needs a signed transaction and gas',
    'init': 'the constructor (#[init]); runs once, batched into the deploy by the workshop',
    'gas / tgas': 'the fee unit for changes; 1 Tgas = 10^12 gas, a call may use up to 300 Tgas; unused gas is refunded',
    'yocto / yoctonear': '10^-24 NEAR, the smallest unit; "one yocto" attached to a call proves a wallet confirmed it',
    'storage staking': 'every byte a contract keeps is NEAR its account must hold (1 N per 100 KB); released when data is deleted',
    'predecessor': 'the account that called this contract (env::predecessor_account_id); what "the caller" means',
    'signer': 'the account whose key signed the whole transaction; in a chain of calls it stays the first one',
    'access key': 'a key on an account: FullAccess (anything) or FunctionCall (one contract, some methods, a gas allowance, no deposits)',
    'function call key': 'the limited key an app gets when you sign in; session auth on chain',
    'borsh': 'the binary format the SDK uses to store your struct; JSON is used for arguments and results',
    'nep-141': 'the fungible token standard (ft_transfer, ft_balance_of, ...); NEP-148 adds metadata, NEP-145 storage deposits',
    'nep-297': 'the event standard: a log line starting with EVENT_JSON:',
    'promise': 'a cross-contract call; it runs in a later block, so you get the result in a callback',
    'callback': 'a #[private] method on your own contract that receives a Promise\'s result',
    'hash': 'a one-way fingerprint: any input to 32 fixed bytes, impossible to reverse (sha256 here, Poseidon in Cairo)',
    'commitment': 'publishing a hash of a value now so you can prove later that you had decided it; rung 1 of ZK',
    'salt': 'random bytes mixed into a commitment so small values cannot be brute-forced',
    'merkle root': 'one hash that stands for a whole list; a member proves membership with log2(N) sibling hashes',
    'nullifier': 'a hash of a member\'s secret, published on claim so a second claim is caught without revealing who claimed',
    'zero-knowledge proof / zk': 'a proof that a statement is true which reveals nothing beyond that; SNARKs are small, STARKs need no trusted setup',
    'snark': 'a succinct ZK proof (Groth16, PLONK); verifiable on NEAR via the alt_bn128 host functions',
    'stark': 'the proof system behind Cairo and Starknet: no trusted setup, hash-based, larger proofs',
    'cairo': 'the language of provable programs and Starknet contracts; the cairo mod on this box is its workbench',
    'starknet': 'a chain whose contracts are Cairo and whose blocks are proven to Ethereum; ZK by construction',
    'poseidon': 'a hash designed to be cheap inside a ZK proof; Cairo\'s default for seals and merkle trees',
    'alt_bn128 / bn254': 'the elliptic curve NEAR exposes as host functions (g1 sum, multiexp, pairing check), enough to verify Groth16 proofs on chain',
    'testnet': 'a public NEAR network with free accounts and free NEAR, same rules as mainnet; the workshop ships there by default',
}


def lesson(lid):
    for l in LESSONS:
        if l['id'] == lid:
            return l
    return None


def index():
    return {'lessons': [{k: l[k] for k in ('id', 'title', 'level', 'minutes', 'summary')}
                        | ({'try': l['try']['template']} if l.get('try') else {})
                        | ({'cairo': l['cairo']} if l.get('cairo') else {})
                        for l in LESSONS],
            'glossary': GLOSSARY, 'count': len(LESSONS)}
