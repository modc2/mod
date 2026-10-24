"""tutorials.py — the LEARN track: stablecoins and Cairo, for complete beginners.

Plain data, no logic. Served by mod.py `tutorials(id=None)`, the console's
LEARN view, the MCP tools `cairo_tutorials` / `cairo_lesson`, and the helper
agent (which quotes lessons back when a question touches their topic).

A lesson is {id, title, level, minutes, summary, topics, sections, try?}.
A section is {h, say, code?, lang?, table?, tip?, warn?}: `say` is plain
English (blank line = new paragraph), `code` a snippet, `table` rows of
strings (first row = header). `try` names a starter template the console can
scaffold in one click, and the steps to do with its app afterwards.
"""

LESSONS = [
    {
        'id': 'what-is-a-stablecoin',
        'title': 'What is a stablecoin?',
        'level': 'start here',
        'minutes': 4,
        'summary': 'A crypto coin that is always worth about $1. Why that is useful, and the three ways people keep it there.',
        'topics': ['stablecoin', 'stable', 'peg', 'usdc', 'usdt', 'dai', 'what is'],
        'sections': [
            {'h': 'The one-sentence answer',
             'say': 'A stablecoin is a token that is meant to be worth exactly one dollar, today, tomorrow and next year.\n\n'
                    'Most crypto (ETH, BTC, STRK) jumps up and down in price. That is exciting to trade, but useless for paying rent. '
                    'A stablecoin gives you crypto\'s good parts (send it anywhere in seconds, no bank hours, programmable) '
                    'without the wild price.'},
            {'h': 'Think of arcade tokens',
             'say': 'At an arcade you hand over $10 and get 10 tokens. Each token is "worth" $1 because the arcade will always swap it back. '
                    'Nobody argues about the price of an arcade token.\n\n'
                    'A stablecoin is the same trick, run by a computer program (a smart contract) instead of a cashier. '
                    'The whole game is: why should anyone believe they can always swap 1 coin back for $1?'},
            {'h': 'The three ways to keep it at $1',
             'say': 'There are only three answers to "why is it worth a dollar?":',
             'table': [
                 ['kind', 'what backs each coin', 'real examples', 'risk'],
                 ['cash-backed', 'a real dollar in a bank', 'USDC, USDT', 'you trust the company + bank'],
                 ['crypto-backed', 'MORE than $1 of crypto locked in a contract', 'DAI, LUSD, CASH (Opus on Starknet)', 'crypto crashes faster than loans close'],
                 ['algorithmic', 'nothing, just a formula and hope', 'UST (collapsed in 2022, lost ~$40B)', 'very high: avoid'],
             ]},
            {'h': 'On Starknet today',
             'say': 'Starknet already has the big ones: native USDC (by far the largest), USDT, DAI, and CASH from the Opus protocol, '
                    'which is crypto-backed and written in Cairo.\n\n'
                    'In this workbench you will build small, readable versions of the first two kinds, and play with them on a private '
                    'practice chain where nothing costs real money.',
             'tip': 'Next lesson: what a smart contract is, in 3 minutes, with no code you have to understand yet.'},
        ],
    },
    {
        'id': 'smart-contracts-101',
        'title': 'Smart contracts, explained like a vending machine',
        'level': 'start here',
        'minutes': 4,
        'summary': 'Storage, functions, events and "who is calling": the four ideas behind every contract, including stablecoins.',
        'topics': ['contract', 'cairo', 'storage', 'function', 'event', 'caller', 'what is cairo', 'starknet'],
        'sections': [
            {'h': 'A vending machine you can\'t argue with',
             'say': 'A smart contract is a program that lives on a blockchain. Like a vending machine: you press a button, it does exactly '
                    'what it was built to do, and nobody (not even its owner) can reach in and change the rules afterwards.\n\n'
                    'Cairo is the language Starknet contracts are written in. "ZK" (zero-knowledge) is how Starknet proves, with math, '
                    'that every contract ran correctly, so the network can check lots of work cheaply.'},
            {'h': 'The four parts',
             'say': 'Every contract, from a counter to a $1B stablecoin, is made of the same four things:',
             'table': [
                 ['part', 'vending-machine version', 'stablecoin version'],
                 ['storage', 'how many snacks are inside', 'everyone\'s balance, the total supply'],
                 ['read function (view)', 'looking through the glass', 'balance_of: "how many coins do I have?"'],
                 ['write function', 'pressing a button', 'transfer, mint, redeem'],
                 ['event', 'the printed receipt', '"alice sent 5 to bob"'],
             ]},
            {'h': 'What it looks like in Cairo',
             'say': 'This is a whole (tiny) contract. Don\'t worry about every word. Find the four parts:',
             'lang': 'cairo',
             'code': '#[storage]\n'
                     'struct Storage {\n'
                     '    balances: Map<ContractAddress, u256>,   // storage: who has what\n'
                     '}\n\n'
                     'fn balance_of(self: @ContractState, who: ContractAddress) -> u256 {\n'
                     '    self.balances.read(who)                 // a read: just looks\n'
                     '}\n\n'
                     'fn transfer(ref self: ContractState, to: ContractAddress, amount: u256) {\n'
                     '    let me = get_caller_address();          // WHO pressed the button\n'
                     '    assert(self.balances.read(me) >= amount, \'not enough coins\');\n'
                     '    self.balances.write(me, self.balances.read(me) - amount);\n'
                     '    self.balances.write(to, self.balances.read(to) + amount);\n'
                     '    self.emit(Transfer { from: me, to, amount });   // the receipt\n'
                     '}'},
            {'h': 'The two words that matter most',
             'say': '`get_caller_address()` is how a contract knows who pressed the button. It can\'t be faked, which is why '
                    '"only the issuer may mint" actually works.\n\n'
                    '`assert(condition, \'message\')` is the contract saying "no". If the condition is false, the whole action is '
                    'cancelled as if it never happened, and you see the message. Every safety rule in a stablecoin is an assert.',
             'tip': 'Ready? The next lesson builds a real cash-backed dollar in about 5 minutes.'},
        ],
    },
    {
        'id': 'build-a-cash-dollar',
        'title': 'Build a cash-backed dollar (like USDC)',
        'level': 'build',
        'minutes': 8,
        'summary': 'Make your own digital dollar where an issuer can only print coins that have a real dollar behind them. Then play with it.',
        'topics': ['usdc', 'cash', 'reserve', 'issuer', 'mint', 'redeem', 'backed', 'make a stablecoin', 'create a stablecoin', 'build a stablecoin', 'launch'],
        'try': {'template': 'stablecoin', 'name': 'my_dollar',
                'steps': ['Press Start, keep the defaults (you are the issuer).',
                          'Cash in the bank: 1000.', 'Make new coins: 100 for bob.',
                          'Try to make 5000 more: it refuses ("not enough cash in reserve").',
                          'Switch to bob and Cash out 40.']},
        'sections': [
            {'h': 'The plan',
             'say': 'Four rules, that\'s the whole coin:\n\n'
                    '1. One account is the issuer (the "bank").\n'
                    '2. The issuer reports how many dollars it holds (reserves).\n'
                    '3. The issuer can make new coins, but never more than the reserves.\n'
                    '4. Anyone can cash out: their coins are destroyed and the bank pays them back.'},
            {'h': 'Step 1: make it',
             'say': 'Press "Make this contract" below (or the + at the top, then pick "Stablecoin (cash-backed)"). '
                    'You get a full Cairo project with the code, 5 tests and a simple app, already written.'},
            {'h': 'Step 2: find THE rule',
             'say': 'Open the CODE tab and look for `fn mint`. This is the line that makes it a stablecoin and not a money printer:',
             'lang': 'cairo',
             'code': 'fn mint(ref self: ContractState, to: ContractAddress, amount: u256) {\n'
                     '    self.only_issuer();                                   // rule 3a: only the bank\n'
                     '    let supply = self.supply.read() + amount;\n'
                     '    assert(supply <= self.reserves.read(), \'not enough cash in reserve\');  // rule 3b\n'
                     '    self.supply.write(supply);\n'
                     '    self.balances.write(to, self.balances.read(to) + amount);\n'
                     '}',
             'tip': 'Anyone can call `is_fully_backed()` at any time to check reserves >= supply. On a real chain that\'s public, forever.'},
            {'h': 'Step 3: test it',
             'say': 'Press Test. Five tests run, each one a little story: "the issuer reports $50 then tries to mint $51: it must fail". '
                    'Green means the rules hold. (The first run takes about a minute while the tools warm up.)'},
            {'h': 'Step 4: play with it',
             'say': 'Open the APP tab. It runs on a private practice chain with four pretend people (alice, bob, carol, dave). '
                    'Switch between them at the top right and follow the steps on the page.'},
            {'h': 'What\'s missing for the real thing',
             'say': 'Real issuers like Circle (USDC) add: an auditor who checks the bank every month, the ability to freeze stolen coins, '
                    'a pause button, minting split across many approved "minters", and the full ERC20 standard (approve / transfer_from) '
                    'so every app can use it. All of those are a sentence away with the agent: try "add a pause button only the issuer can use".',
             'warn': 'The reserves number is only as honest as the issuer. The contract enforces "no more coins than reported cash", '
                     'it cannot see the bank account.'},
        ],
    },
    {
        'id': 'build-a-crypto-dollar',
        'title': 'Build a crypto-backed dollar (like DAI)',
        'level': 'build',
        'minutes': 10,
        'summary': 'No bank: people lock up crypto and borrow dollars against it. Learn collateral, the 150% rule and liquidations by playing them.',
        'topics': ['dai', 'collateral', 'vault', 'borrow', 'liquidat', 'ratio', 'oracle', 'price', 'overcollateral', 'cdp', 'opus', 'cash'],
        'try': {'template': 'vault_stable', 'name': 'my_vault',
                'steps': ['Start with ETH at $3000.', 'Lock up 1 ETH, Borrow 2000.',
                          'Set the ETH price to 2500: your loan turns UNSAFE.',
                          'As bob: lock 10 ETH, borrow 2000, Close alice\'s unsafe loan.']},
        'sections': [
            {'h': 'The pawn-shop idea',
             'say': 'You bring a $3000 watch to a pawn shop. They lend you $2000 cash and keep the watch. Pay back $2000, get the watch back.\n\n'
                    'The shop lends less than the watch is worth on purpose: if watch prices fall a bit, the loan is still covered. '
                    'A crypto-backed stablecoin is a pawn shop run by a contract, where the "cash" it lends is brand-new stablecoins.'},
            {'h': 'The 150% rule, with numbers',
             'say': 'You must keep at least $1.50 of crypto behind every $1 you borrow. That cushion is why each coin stays worth $1.',
             'table': [
                 ['ETH price', 'your 1 ETH is worth', 'you owe', 'safety level', 'status'],
                 ['$3000', '$3000', '$2000', '150%', 'safe (just)'],
                 ['$4000', '$4000', '$2000', '200%', 'very safe'],
                 ['$2500', '$2500', '$2000', '125%', 'UNSAFE: anyone may close it'],
                 ['$1800', '$1800', '$2000', '90%', 'too late: the system lost money'],
             ],
             'tip': 'The safety level is just: value of what you locked / what you owe. Keep it well above 150%.'},
            {'h': 'Liquidation: the self-cleaning part',
             'say': 'When a loan drops under 150%, anyone can "liquidate" it: they pay off that loan with their own stablecoins and '
                    'receive the locked crypto, which is worth more than they paid. That profit is the reward for keeping the system healthy.\n\n'
                    'Because someone always has a reason to close bad loans early, the coins out there always have more than $1 behind them.',
             'lang': 'cairo',
             'code': 'fn liquidate(ref self: ContractState, user: ContractAddress) {\n'
                     '    assert(!self.is_safe(user), \'that loan is still safe\');\n'
                     '    let by = get_caller_address();\n'
                     '    self.burn(by, self.debt.read(user));          // liquidator pays the debt\n'
                     '    let col = self.collateral.read(user);\n'
                     '    self.collateral.write(user, 0);\n'
                     '    self.collateral.write(by, self.collateral.read(by) + col);  // and gets the ETH\n'
                     '    self.debt.write(user, 0);\n'
                     '}'},
            {'h': 'Where does the price come from?',
             'say': 'A contract can\'t look up prices on the internet. An "oracle" brings them on chain. In this lesson one account (the oracle) '
                    'simply types the price in, so you can crash ETH yourself and watch what happens.\n\n'
                    'On real Starknet you would read Pragma, an oracle network that already publishes ETH/USD, USDC/USD and more.',
             'warn': 'The "ETH" here is pretend: deposit just adds to a number. A real vault pulls a real token in with transfer_from '
                     'and sends it back on withdraw. Ask the agent to "use a real ERC20 as collateral" when you are ready.'},
            {'h': 'Try it',
             'say': 'Make the contract, press Test (6 tests, including a full price-crash-and-liquidation story), then open the APP '
                    'tab and follow its steps. The "Safety level" bar is the thing to watch.'},
        ],
    },
    {
        'id': 'test-like-a-pro',
        'title': 'Tests: proving your coin can\'t be broken',
        'level': 'build',
        'minutes': 5,
        'summary': 'How snforge tests read like little stories, how to pretend to be someone else, and how to test that bad things fail.',
        'topics': ['test', 'snforge', 'should_panic', 'cheat', 'fail', 'pass', 'bug'],
        'sections': [
            {'h': 'A test is a tiny story',
             'say': 'Each test sets up a fresh contract, does a few things, and checks the result. If any check fails, the test goes red.',
             'lang': 'cairo',
             'code': '#[test]\n'
                     'fn test_mint_needs_cash_then_works() {\n'
                     '    let c = deploy();                                  // a brand-new coin\n'
                     '    start_cheat_caller_address(c.contract_address, issuer());   // "pretend I am the issuer"\n'
                     '    c.set_reserves(100);\n'
                     '    c.mint(alice(), 100);\n'
                     '    assert(c.balance_of(alice()) == 100, \'alice has 100\');\n'
                     '}'},
            {'h': 'Testing that the bad thing FAILS',
             'say': 'For money, the most important tests are the ones that try to cheat. `should_panic` means "this test passes only if '
                    'the contract says no, with exactly this message":',
             'lang': 'cairo',
             'code': '#[test]\n'
                     '#[should_panic(expected: \'not enough cash in reserve\')]\n'
                     'fn test_cannot_print_unbacked() {\n'
                     '    let c = deploy();\n'
                     '    start_cheat_caller_address(c.contract_address, issuer());\n'
                     '    c.set_reserves(50);\n'
                     '    c.mint(alice(), 51);       // must be refused\n'
                     '}',
             'tip': 'A good rule: for every assert in your contract, write one test that trips it.'},
            {'h': 'Reading the results',
             'say': 'The TEST tab shows each test with a green or red mark, how much "gas" (work) it used, and for a red one, why it failed. '
                    'Ask the agent "why did my test fail?" and it will read the last run for you.'},
        ],
    },
    {
        'id': 'make-it-yours',
        'title': 'Make it yours with the agent',
        'level': 'level up',
        'minutes': 4,
        'summary': 'Describe a change in one sentence; the coding agent edits the Cairo, rebuilds and re-runs the tests for you.',
        'topics': ['vibe', 'agent', 'change', 'add', 'feature', 'pause', 'freeze', 'fee', 'interest'],
        'sections': [
            {'h': 'One sentence in, working code out',
             'say': 'Open a contract, go to CODE, and type what you want into the agent box. It edits the contract and its tests, then builds '
                    'and tests again so you know it still works. (Only the owner of this workbench can run it, since it uses a paid AI account.)'},
            {'h': 'Ideas for your stablecoin',
             'table': [
                 ['say this', 'what you get'],
                 ['add a pause button only the issuer can use', 'an emergency stop for transfers'],
                 ['let the issuer freeze and unfreeze an address', 'how USDC handles stolen funds'],
                 ['charge a 0.1% fee on redeem that goes to the issuer', 'a business model'],
                 ['add approve and transfer_from like ERC20', 'other apps can move coins for you'],
                 ['charge 2% yearly interest on vault debt', 'a "stability fee" like DAI'],
                 ['give liquidators only a 10% bonus and return the rest', 'a fairer liquidation'],
             ]},
            {'h': 'Ask the helper',
             'say': 'The round button in the bottom-right corner is a helper that knows these lessons and can look at your contracts: '
                    'what a function does, whether your loan is safe, why a test failed. Try "explain my_vault like I\'m 10".'},
        ],
    },
    {
        'id': 'going-live',
        'title': 'From practice to real money: what changes',
        'level': 'level up',
        'minutes': 4,
        'summary': 'The honest checklist between a practice-chain stablecoin and one people trust with real dollars.',
        'topics': ['mainnet', 'deploy', 'real', 'live', 'audit', 'legal', 'risk', 'production', 'safe'],
        'sections': [
            {'h': 'The practice chain is a sandbox',
             'say': 'Everything here runs on a private local chain (a "devnet") with pretend accounts. Mistakes cost nothing, '
                    'which is exactly why it\'s the place to learn. Nothing you do here touches real Starknet.'},
            {'h': 'The checklist',
             'table': [
                 ['step', 'why'],
                 ['real collateral / real reserves', 'pretend ETH and self-reported cash are fine to learn with, not to trust'],
                 ['a real price oracle (Pragma)', 'one person typing the price is a single point of failure'],
                 ['tests for every way to cheat', 'every assert gets a test that trips it'],
                 ['a professional audit', 'people lose millions to bugs tests didn\'t imagine'],
                 ['testnet first (Sepolia)', 'real network, fake money'],
                 ['legal advice', 'issuing a dollar token is regulated in most countries'],
             ],
             'warn': 'Not financial or legal advice. These lessons teach how stablecoins work; launching one for real is a serious undertaking.'},
        ],
    },
]

GLOSSARY = {
    'stablecoin': 'a token designed to stay worth $1 (or another fixed amount)',
    'peg': 'the price a stablecoin is supposed to hold, e.g. $1. "Losing the peg" = drifting away from it',
    'collateral': 'something valuable locked up to back a loan, like the watch at a pawn shop',
    'reserves': 'the real dollars an issuer holds to back its coins',
    'issuer': 'the company or account allowed to create (mint) new coins',
    'mint': 'create new coins',
    'burn': 'destroy coins, taking them out of existence',
    'redeem': 'swap coins back for what backs them (dollars or collateral)',
    'liquidation': 'closing an unsafe loan by paying its debt and taking its collateral',
    'collateral ratio': 'value locked / value borrowed. 150% means $1.50 behind every $1',
    'oracle': 'a service that brings outside facts (like the ETH price) onto the blockchain',
    'smart contract': 'a program on a blockchain whose rules nobody can change after it starts',
    'cairo': 'the programming language for Starknet contracts, built for zero-knowledge proofs',
    'starknet': 'a network on top of Ethereum that uses ZK proofs to run contracts cheaply',
    'zk / zero-knowledge': 'math that proves a computation was done right without redoing it',
    'devnet': 'a private practice blockchain on this machine; nothing real is at stake',
    'gas': 'the amount of work an action takes, which is what fees are charged on',
    'u256': 'a whole number type big enough for any token amount',
    'decimals': 'how many digits after the point a token has; 18 means 1 coin = 1000000000000000000 units',
    'assert': 'a rule in the code: if it\'s false, the whole action is cancelled',
    'event': 'a receipt a contract writes when something happens, e.g. "alice sent 5 to bob"',
    'erc20': 'the standard set of functions (transfer, balance_of, approve...) every token speaks',
}


def lesson(lid):
    for x in LESSONS:
        if x['id'] == lid:
            return x
    return None


def index():
    return [{k: x[k] for k in ('id', 'title', 'level', 'minutes', 'summary')}
            | ({'template': x['try']['template']} if x.get('try') else {})
            for x in LESSONS]
