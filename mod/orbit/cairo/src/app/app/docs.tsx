'use client';

import { useEffect, useMemo, useState } from 'react';
import { highlightLine } from './highlight';
import { BASE, call } from './lib';

function Code({ src, cairo = true }: { src: string; cairo?: boolean }) {
  const html = useMemo(
    () =>
      src
        .trim()
        .split('\n')
        .map((l) => (cairo ? highlightLine(l) : l.replace(/&/g, '&amp;').replace(/</g, '&lt;')) || ' ')
        .join('\n'),
    [src, cairo],
  );
  return <pre className="dcode" dangerouslySetInnerHTML={{ __html: html }} />;
}

const ANATOMY = `
#[starknet::interface]
pub trait IVault<TContractState> {
    fn balance_of(self: @TContractState, who: ContractAddress) -> u256;
    fn deposit(ref self: TContractState, amount: u256);
}

#[starknet::contract]
pub mod Vault {
    use starknet::{ContractAddress, get_caller_address};
    use starknet::storage::{Map, StorageMapReadAccess, StorageMapWriteAccess};

    #[storage]
    struct Storage {
        balances: Map<ContractAddress, u256>,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event { Deposited: Deposited }

    #[derive(Drop, starknet::Event)]
    pub struct Deposited { #[key] pub who: ContractAddress, pub amount: u256 }

    #[abi(embed_v0)]
    impl VaultImpl of super::IVault<ContractState> {
        fn balance_of(self: @ContractState, who: ContractAddress) -> u256 {
            self.balances.read(who)
        }
        fn deposit(ref self: ContractState, amount: u256) {
            assert(amount > 0, 'zero amount');
            let who = get_caller_address();
            self.balances.write(who, self.balances.read(who) + amount);
            self.emit(Deposited { who, amount });
        }
    }
}`;

const TEST = `
use snforge_std::{declare, ContractClassTrait, DeclareResultTrait, start_cheat_caller_address};
use vault::{IVaultDispatcher, IVaultDispatcherTrait};

fn deploy() -> IVaultDispatcher {
    let class = declare("Vault").unwrap().contract_class();
    let (address, _) = class.deploy(@array![]).unwrap();
    IVaultDispatcher { contract_address: address }
}

#[test]
fn deposit_credits_the_caller() {
    let v = deploy();
    let alice = 0x123.try_into().unwrap();
    start_cheat_caller_address(v.contract_address, alice);
    v.deposit(5);
    assert(v.balance_of(alice) == 5, 'not credited');
}

#[test]
#[should_panic(expected: ('zero amount',))]
fn zero_is_rejected() {
    deploy().deposit(0);
}`;

const CHEATS: [string, string][] = [
  ['felt252', "the native field element; short strings like 'hi' fit in one (31 bytes)"],
  ['u8 … u256', 'checked integers; u256 is two felts (low, high) on the wire'],
  ['ContractAddress', 'an account or contract address'],
  ['ByteArray', 'arbitrary-length text, written "like this"'],
  ['Map<K, V>', 'storage mapping; read with .read(k), write with .write(k, v)'],
  ['@T / ref T', 'snapshot (read-only view) / mutable reference'],
  ["assert(c, 'msg')", 'revert with a short-string reason when c is false'],
  ['get_caller_address()', 'who called this entrypoint'],
  ['get_block_timestamp()', 'the block time, seconds'],
  ['poseidon_hash_span', 'the STARK-friendly hash; cheap in-circuit'],
];

type Section = { id: string; title: string };
const SECTIONS: Section[] = [
  { id: 'what', title: 'What this is' },
  { id: 'quick', title: 'Quickstart' },
  { id: 'anatomy', title: 'Anatomy of a contract' },
  { id: 'tests', title: 'Testing with snforge' },
  { id: 'sandbox', title: 'The sandbox and apps' },
  { id: 'vibe', title: 'Vibecoding' },
  { id: 'cli', title: 'CLI' },
  { id: 'http', title: 'HTTP API' },
  { id: 'cheats', title: 'Cairo cheatsheet' },
  { id: 'tools', title: 'Toolchain' },
];

// DOCS: how the workbench works, enough Cairo to read and write the starters,
// and the full HTTP surface (pulled live from the server's own endpoint map).
export function Docs() {
  const [info, setInfo] = useState<any>(null);
  const [at, setAt] = useState('what');

  useEffect(() => {
    call('info').then(setInfo).catch(() => {});
  }, []);

  useEffect(() => {
    const els = SECTIONS.map((s) => document.getElementById(`d-${s.id}`)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver(
      (es) => {
        const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (v) setAt(v.target.id.slice(2));
      },
      { rootMargin: '-80px 0px -60% 0px' },
    );
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, []);

  const go = (id: string) => document.getElementById(`d-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  const endpoints = Object.entries((info?.endpoints || {}) as Record<string, string>).filter(([k]) => k !== 'note');
  const tools = info?.toolchain || {};

  return (
    <div className="docs">
      <nav className="toc">
        {SECTIONS.map((s) => (
          <button key={s.id} className={at === s.id ? 'on' : ''} onClick={() => go(s.id)}>
            {s.title}
          </button>
        ))}
      </nav>
      <article className="dbody">
        <section id="d-what">
          <div className="eyebrow">docs</div>
          <h1>A workbench for zero-knowledge contracts.</h1>
          <p>
            Cairo is the language of Starknet: every transaction is proven with a STARK, so a contract is a program whose
            execution can be verified without being re-run. This module lets you write those programs, compile them, test
            them, and run them against a local chain, all from one page.
          </p>
          <p>
            Each contract is its own <b>mod</b>: a self-contained Scarb project at <code>src/mods/&lt;name&gt;/</code>,
            addressed <code>cairo.&lt;name&gt;</code>, with its own app at <code>{BASE}/&lt;name&gt;/</code>.
          </p>
          <div className="dgrid">
            <div>
              <b>Studio</b>
              <span>Edit, build, test, vibecode one contract. Its App, API, Code and Test tabs.</span>
            </div>
            <div>
              <b>Market</b>
              <span>Every contract on this box as a listing: open, try, fork.</span>
            </div>
            <div>
              <b>New</b>
              <span>Start from a starter or a fork, and optionally hand the agent a sentence.</span>
            </div>
          </div>
        </section>

        <section id="d-quick">
          <h2>Quickstart</h2>
          <ol className="dsteps">
            <li>
              <b>New</b> → pick <i>Counter</i>, name it, Make. You land in the Studio on its source.
            </li>
            <li>
              <b>Build</b> compiles to Sierra and CASM. Errors show as red lines; click one to jump.
            </li>
            <li>
              <b>Test</b> runs snforge: every test with its gas and, on failure, the revert reason.
            </li>
            <li>
              <b>App</b> deploys it to the local devnet and gives you a button per entrypoint.
            </li>
            <li>
              <b>Vibe</b> a change in plain words. The agent edits only this contract, then builds and tests it.
            </li>
          </ol>
        </section>

        <section id="d-anatomy">
          <h2>Anatomy of a contract</h2>
          <p>
            An <b>interface</b> trait declares the entrypoints. The <b>contract</b> module holds storage, events and the
            impl. <code>self: @ContractState</code> marks a read (a view); <code>ref self</code> marks a write that costs a
            transaction.
          </p>
          <Code src={ANATOMY} />
          <p className="muted">
            Every interface also generates a <code>IVaultDispatcher</code> other contracts and tests use to call it.
          </p>
        </section>

        <section id="d-tests">
          <h2>Testing with snforge</h2>
          <p>
            Tests live in <code>tests/</code> and deploy the contract into a fresh in-memory chain. Cheatcodes change who is
            calling, the block time, and more. A test that expects a revert names the reason.
          </p>
          <Code src={TEST} />
          <p className="muted">
            The first test run of a contract compiles the test crate (about 30 seconds). There is no incremental compile, so
            every run takes roughly that long.
          </p>
        </section>

        <section id="d-sandbox">
          <h2>The sandbox and apps</h2>
          <p>
            A local <b>starknet-devnet</b> runs next to the workbench with four funded accounts: <code>alice</code>,{' '}
            <code>bob</code>, <code>carol</code>, <code>dave</code>. It keeps its state across restarts. Nothing touches a
            real network.
          </p>
          <p>
            Every built contract gets an app at <code>{BASE}/&lt;name&gt;/</code>: read cards that refresh themselves,
            write cards that send a transaction as whichever account you pick, a deploy form for constructor arguments, and
            a feed of decoded events. Drop an <code>app/index.html</code> into the contract to replace it with your own.
          </p>
        </section>

        <section id="d-vibe">
          <h2>Vibecoding</h2>
          <p>
            Vibe runs a coding agent scoped to one contract directory. It knows the Cairo 2.8 rules and the snforge API,
            edits the sources and tests, and the workbench builds and tests afterwards so you see whether it worked. Good
            prompts name the behaviour and the failure cases:
          </p>
          <Code cairo={false} src={`add an owner set in the constructor; only the owner can call set.\nreject zero, emit an event on every change, and test both reverts.`} />
          <p className="muted">Writes, builds, tests and vibes are owner-only. Sign in with the owner wallet to use them.</p>
        </section>

        <section id="d-cli">
          <h2>CLI</h2>
          <Code
            cairo={false}
            src={`m cairo/new name=vault template=token
m cairo/fork src=airdrop name=drop2
m cairo.vault/build
m cairo.vault/test
m cairo.vault/vibe prompt="add a pause switch"
m cairo.vault/deploy
m cairo.vault/call fn=balance_of args='{"who":"alice"}'
m cairo/market`}
          />
        </section>

        <section id="d-http">
          <h2>HTTP API</h2>
          <p>
            Base <code>{origin}{BASE}/api</code>. Reads are open. Writes take a mod-protocol token in a <code>token</code>{' '}
            header. Long calls accept <code>{'{"async": true}'}</code> and return a job to poll at <code>/job?id=</code>.
          </p>
          <div className="eps">
            {endpoints.length === 0 && <div className="muted">loading…</div>}
            {endpoints.map(([k, v]) => {
              const [method, path] = k.split(' ');
              return (
                <div className="ep" key={k}>
                  <span className={`meth ${method.toLowerCase()}`}>{method}</span>
                  <code>{path}</code>
                  <span>{v}</span>
                </div>
              );
            })}
          </div>
        </section>

        <section id="d-cheats">
          <h2>Cairo cheatsheet</h2>
          <div className="cheats">
            {CHEATS.map(([k, v]) => (
              <div key={k}>
                <code>{k}</code>
                <span>{v}</span>
              </div>
            ))}
          </div>
        </section>

        <section id="d-tools">
          <h2>Toolchain</h2>
          <div className="cheats">
            {Object.entries(tools).map(([k, v]: [string, any]) => (
              <div key={k}>
                <code>{k}</code>
                <span>{v?.version || (v?.path ? 'present' : 'missing')}</span>
              </div>
            ))}
            {!info && <div className="muted">loading…</div>}
          </div>
        </section>
      </article>
    </div>
  );
}
