// SubWallet compatibility check for bt/tx.py — signs the node's SignerPayloadJSON
// exactly the way @polkadot/extension-base does (SubWallet, Talisman and
// polkadot{.js} all share that code), so the node can verify the bytes match.
//
//   cd /tmp/pjs && npm i @polkadot/api @polkadot/keyring @polkadot/util-crypto
//   NODE_PATH=/tmp/pjs/node_modules node scripts/signpayload_check.js '<payload json>' '<mnemonic|//Alice>' [plain|meta]
//
// plain = registry with no metadata (a wallet that has never seen Bittensor);
// meta  = registry built from live finney metadata (a wallet that has).
// Prints {address, signature}; POST the signature to /api/tx/submit, or check
// it offline with bt.tx.verify(address, sign_bytes, signature).
const { TypeRegistry } = require('@polkadot/types');
const { Keyring } = require('@polkadot/keyring');
const { cryptoWaitReady } = require('@polkadot/util-crypto');

(async () => {
  await cryptoWaitReady();
  const [payloadJson, uri, mode] = process.argv.slice(2);
  const payload = JSON.parse(payloadJson);
  const pair = new Keyring({ type: 'sr25519', ss58Format: 42 }).addFromUri(uri);
  let registry, api;
  if (mode === 'meta') {
    const { ApiPromise, WsProvider } = require('@polkadot/api');
    api = await ApiPromise.create({ provider: new WsProvider('wss://entrypoint-finney.opentensor.ai:443'), noInitWarn: true });
    registry = api.registry;
  } else registry = new TypeRegistry();
  registry.setSignedExtensions(payload.signedExtensions);
  const r = registry.createType('ExtrinsicPayload', payload, { version: payload.version }).sign(pair);
  console.log(JSON.stringify({ address: pair.address, ...r }));
  if (api) await api.disconnect();
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
