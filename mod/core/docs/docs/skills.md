# Skills Reference

Everything you can do with mod, organized by what you're trying to accomplish.

## Module Management

Load, discover, inspect, and create modules across the entire ecosystem.

| Skill | Python | CLI |
|-------|--------|-----|
| Load a module | `m.mod('agent')()` | — |
| Call a function | `m.fn('agent/forward')(query="x")` | `m agent/forward query=x` |
| List all modules | `m.mods()` | `m mods` |
| Search modules | `m.search('agent')` | `m search agent` |
| Module tree | `m.tree()` | `m tree` |
| Module info | `m.info('agent')` | `m info agent` |
| List functions | `m.fns('agent')` | `m fns agent` |
| Function schema | `m.schema('agent')` | `m schema agent` |
| View source code | `m.code('agent')` | `m code agent` |
| Get directory path | `m.dirpath('agent')` | `m dp agent` |
| Check if exists | `m.mod_exists('agent')` | — |
| Get file contents | `m.content('agent')` | `m content agent` |
| Count files | `m.nfiles('agent')` | `m nfiles agent` |
| Create new module | `m.new('mymod')` | `m new mymod` |
| Copy module | `m.cpmod('src', 'dst')` | `m cpmod src dst` |
| Remove module | `m.rmmod('mymod')` | `m rmmod mymod` |
| Import from CID | `m.addcid('name', 'Qm...')` | `m addcid name Qm...` |
| Import from path | `m.addpath('/path')` | `m addpath /path` |
| Update tree cache | `m.update()` | `m update` |

## Cryptographic Keys

Multi-chain identity — Ethereum, Substrate, Solana. Sign, verify, encrypt, decrypt.

| Skill | Python | CLI |
|-------|--------|-----|
| Get/create key | `m.get_key('main')` | `m get_key main` |
| List keys | `m.keys()` | `m keys` |
| Sign data | `m.sign(data, key='main')` | `m sign '{"msg":"hi"}' key=main` |
| Verify signature | `m.verify(data, signature=sig, address=addr)` | `m verify ...` |
| Encrypt data | `m.encrypt(data, key='main')` | `m encrypt ...` |
| Decrypt data | `m.decrypt(data, key='main')` | `m decrypt ...` |
| Key → address map | `m.key2address()` | `m key2address` |
| Address → key map | `m.address2key()` | `m address2key` |
| Generate mnemonic | `m.mnemonic(words=24)` | `m mnemonic words=24` |
| Get address | `m.addy('main')` | `m addy main` |
| Owner address | `m.owner()` | `m owner` |
| Check ownership | `m.is_owner('0x...')` | — |
| Hash data | `m.hash(data, mode='sha256')` | `m hash data` |

## Storage

Persist data locally with optional encryption. IPFS integration for decentralized storage.

| Skill | Python | CLI |
|-------|--------|-----|
| Store data | `m.put('key', value)` | `m put key '{"x":1}'` |
| Retrieve data | `m.get('key')` | `m get key` |
| Store encrypted | `m.put('k', v, encrypt=True, password='p')` | `m put k v encrypt=true password=p` |
| Retrieve encrypted | `m.get('k', password='p')` | `m get k password=p` |
| Read JSON | `m.get_json('/path')` | `m get_json /path` |
| Write JSON | `m.put_json('/path', data)` | `m put_json /path data` |
| Read text | `m.get_text('/path')` | `m get_text /path` |
| Write text | `m.put_text('/path', text)` | `m put_text /path text` |
| Check if CID | `m.iscid('Qm...')` | — |
| Cache decorator | `m.cache('path', max_age=60)` | — |
| Delete | `m.rm('key')` | `m rm key` |

## Servers

Serve any module as an HTTP API. PM2 process management with service discovery.

| Skill | Python | CLI |
|-------|--------|-----|
| Start server | `m.serve('api', port=8000)` | `m serve api port=8000` |
| Stop server | `m.kill('api')` | `m kill api` |
| Stop all servers | `m.kill_all()` | `m kill_all` |
| List servers | `m.servers()` | `m servers` |
| Server exists? | `m.server_exists('api')` | — |
| Ensure running | `m.ensure_server('api')` | — |
| Server URLs | `m.urls()` | `m urls` |
| Server namespace | `m.namespace()` | `m namespace` |
| View logs | `m.logs('api')` | `m logs api` |
| Docker up | `m.up('mod')` | `m up mod` |
| Docker down | `m.down('mod')` | `m down mod` |
| Docker build | `m.build()` | `m build` |
| Docker exec | `m.exec('mod', 'cmd')` | `m exec mod cmd` |
| Docker enter | `m.enter('image')` | `m enter image` |

## AI & Agents

Query AI models, run agentic workflows, get help with code.

| Skill | Python | CLI |
|-------|--------|-----|
| Ask AI | `m.ask("question")` | `m ask "question"` |
| Ask about module | `m.about('store', 'what is this?')` | `m about store "what is this?"` |
| How to use | `m.how('agent', 'how do I run this?')` | `m how agent "how do I?"` |
| Get help | `m.help('mod', 'explain this')` | `m help mod "explain this"` |
| AI edit | `m.edit("add tests", mod='mymod')` | `m edit "add tests" mod=mymod` |
| Run agent | `m.fn('agent/forward')(query="build X")` | `m agent/forward query="build X"` |
| Module description | `m.desc('agent')` | `m desc agent` |

## File System

Navigate, list, and manage files and directories.

| Skill | Python | CLI |
|-------|--------|-----|
| List files | `m.files('./path')` | `m files ./path` |
| List folders | `m.folders('./path')` | `m folders ./path` |
| List directory | `m.ls('./path')` | `m ls ./path` |
| Glob pattern | `m.glob('./path')` | `m glob ./path` |
| Absolute path | `m.abspath('~/file')` | — |
| Relative path | `m.relpath('/full/path')` | — |
| Current directory | `m.pwd()` | — |
| File path of mod | `m.filepath('agent')` | `m fp agent` |
| Dockerfiles | `m.dockerfiles('mod')` | `m dockerfiles mod` |
| Read all content | `m.text('./path')` | `m text ./path` |
| Module size | `m.size('agent')` | `m size agent` |

## Git & Version Control

Push code, clone modules, manage repos.

| Skill | Python | CLI |
|-------|--------|-----|
| Push changes | `m.push("commit msg")` | `m push "commit msg"` |
| Clone module | `m.clone('mod', 'name')` | `m clone mod name` |
| List repos | `m.repos()` | `m repos` |
| Repo → path | `m.repo2path()` | `m repo2path` |
| Register on API | `m.reg('mymod')` | `m reg mymod` |
| Rollback version | `m.setback('mod', cid='Qm...')` | `m setback mod cid=Qm...` |

## API & Networking

Remote calls, API client, host management.

| Skill | Python | CLI |
|-------|--------|-----|
| API call | `m.call('api/edit', params={})` | — |
| Get client | `m.client()` | — |
| List hosts | `m.hosts()` | `m hosts` |
| Current host | `m.host()` | `m host` |
| Auth token | `m.token()` | `m token` |

## System & Utilities

Time, hashing, ports, environment, concurrency.

| Skill | Python | CLI |
|-------|--------|-----|
| Current time | `m.time()` | `m time` |
| Time to string | `m.time2str()` | `m time2str` |
| Available ports | `m.get_ports(3)` | `m get_ports 3` |
| Port range | `m.get_port_range()` | — |
| Free port | `m.free_port()` | `m free_port` |
| Used ports | `m.used_ports()` | `m used_ports` |
| Env variable | `m.env('HOME')` | `m env HOME` |
| All env vars | `m.env()` | `m env` |
| Sleep | `m.sleep(1.0)` | — |
| Confirm prompt | `m.confirm("Sure?")` | — |
| Print (rich) | `m.print("text", color='green')` | — |
| Thread executor | `m.executor(max_workers=8)` | — |
| Submit async | `m.submit('mod/fn')` | — |
| Event loop | `m.loop()` | — |

## Configuration

Manage module and framework configuration.

| Skill | Python | CLI |
|-------|--------|-----|
| Get config | `m.config('agent')` | `m config agent` |
| Save config | `m.save_config('agent', {})` | — |
| Config paths | `m.config_paths('agent')` | `m config_paths agent` |
| Config path | `m.config_path('agent')` | — |
| Shortcuts | `m.shortcuts` | — |

## Introspection & Development

Inspect objects, test code, explore the framework.

| Skill | Python | CLI |
|-------|--------|-----|
| Run tests | `m.test('mymod')` | `m test mymod` |
| Run pytest | `m.pytest('mymod')` | `m pytest mymod` |
| List classes | `m.classes('./path')` | `m classes ./path` |
| Dir of object | `m.dir('agent')` | `m dir agent` |
| Has attribute? | `m.hasattr('agent', 'fn')` | — |
| Get args | `m.get_args(fn)` | — |
| CID of module | `m.cid('agent')` | `m cid agent` |
| Verify module info | `m.verify_info('agent')` | — |
| Open in VS Code | `m.vs('./path')` | `m vs ./path` |
| Open in editor | `m.go('agent')` | `m go agent` |
| Full setup | `m.setup()` | `m setup` |
| Readmes | `m.readmes('agent')` | `m readmes agent` |
| Read readme | `m.readme('agent')` | `m readme agent` |
| List utilities | `m.utils()` | `m utils` |

## IPFS Storage (Orbit Module)

Decentralized content-addressed storage.

```python
ipfs = m.mod('ipfs')()

ipfs.put({'key': 'value'})          # Store → CID
ipfs.get('QmCID...')                # Retrieve by CID
ipfs.add_file('/path/to/file')     # Store file → CID
ipfs.get_file('QmCID...')          # Retrieve file
ipfs.pin_add('QmCID...')           # Pin content
ipfs.pin_rm('QmCID...')            # Unpin content
ipfs.pins()                         # List pins
ipfs.start_node()                   # Start daemon
ipfs.stop_node()                    # Stop daemon
ipfs.node_status()                  # Check daemon
```

## Agent Workflows (Orbit Module)

Autonomous AI task execution with tools.

```python
agent = m.mod('agent')()

agent.forward(
    query="Build a REST API",
    path="/project",
    tools=['cmd', 'git', 'deploy'],
    steps=10
)
```

## Claude Code Integration (Orbit Module)

Programmatic access to Claude for code operations.

```python
claude = m.mod('claude')()

claude.ask("Explain this code")
claude.analyze_code("/path/file.py")
claude.generate_code("FastAPI server")
claude.refactor("/path/messy.py")
claude.debug("/path/broken.py")
claude.edit_file("/path/file.py", instructions="Add error handling")

# Background jobs
job_id = claude.submit("Long task")
claude.jobs()
claude.job(job_id)
claude.cancel(job_id)
```

## Uniswap DEX Strategies (Orbit Module)

Multi-chain trading strategies with Rust backend.

```python
uni = m.mod('uniswap')()

uni.quote(chain_id=8453, token_in=usdc, token_out=weth, amount_in=1000000)
uni.build_swap(...)
uni.pool_state(chain_id=8453, pool=pool_addr)
uni.create_strategy(type='dca', interval=3600, amount=100)
uni.create_strategy(type='limit', target_price=2000)
uni.create_strategy(type='momentum', short_window=10, long_window=30)
uni.create_strategy(type='copy_trade', wallet='0xTrader...')
uni.list_strategies()
uni.pause_strategy(strategy_id)
```

## BlocTime Protocol (On-Chain)

Smart contract interactions on Base Sepolia.

```python
api = m.mod('api')()

# Market
api.credit(stable_amount=100, payment_token='USDC')
api.balance(address='0x...', token='USDC')

# Registry
api.register('mymod')

# Transaction building
api.build_transaction(to='0x...', data='0x...', value=0)
api.encode_function_call(contract='Market', function='credit', args=[...])
```

## Bridge (Orbit Module)

Cross-chain token bridge for Substrate → EVM.

```python
bridge = m.mod('bridge')()

bridge.claim(auth_token='signed', recipient='0xEVM...')
bridge.process_claim(address='5Sub...', recipient='0x...', amount=100)
bridge.batch_process_claims()
bridge.unclaimed()
bridge.burn(address='0x...', amount=50)
```
