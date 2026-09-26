# block tests

Most blocks in this catalog are small enough that `solc` compiling them and the
graph validator refusing a bad wiring is the whole safety story. The BlocTime
Treasury is not: it holds other people's money, it decides who gets paid, and it
promises that a lock cannot be undone early. So it has a real test suite, run
against a real EVM.

`treasury.test.js` covers the parts that would be expensive to get wrong:

* the window is BlocTime's, to the second, and every one it names really is a
  Friday at 17:00 UTC;
* a payout cannot happen early and cannot happen twice in a week;
* an escrowed principal comes back only after the term, and the owner's
  `rescue()` cannot reach the asset at all;
* a streamed principal leaves in equal slices with the remainder in the last
  one, so no dust is stranded inside a lock;
* the split follows BLOC *at payout time*, not at registration — a holder who
  sold gets less, and a holder who never registered gets exactly nothing;
* rounding dust stays in the pot instead of being gifted to whoever the loop
  reached last;
* a claim can never eat a lock's principal.

## running them

This module has no JavaScript toolchain of its own — the API is Rust and the
contracts are compiled by `solc` directly. Borrow the `bloctime` module's
hardhat, which is the same one that tests BlocTime.sol itself:

```sh
T=$(mktemp -d)
mkdir -p $T/contracts $T/test
cp -r /root/mod/mod/orbit/bloctime/node_modules $T/node_modules
cp src/api/blocks/common.sol src/api/blocks/treasury.sol $T/contracts/
cp src/api/blocks/tests/Mocks.sol                        $T/contracts/
cp src/api/blocks/tests/treasury.test.js                 $T/test/
cat > $T/hardhat.config.js <<'JS'
require("@nomicfoundation/hardhat-toolbox");
module.exports = {
  solidity: { version: "0.8.24", settings: { optimizer: { enabled: true, runs: 200 } } },
};
JS
(cd $T && ./node_modules/.bin/hardhat test)
```

Copy `node_modules`, never symlink it — a symlink here has wiped a live
module's dependencies before. Use bloctime's LOCAL hardhat binary rather than
`npx hardhat`, which pulls hardhat 3 and fails on this host's Node 18.

`m defi/test_contract` does all of the above for you.
