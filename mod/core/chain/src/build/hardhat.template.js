// Hardhat config for a builder test sandbox (copied in by /build/test).
//
// The sandbox has no dependencies of its own — `node_modules` is a symlink to
// the chain module's. That also gives us solc as a JS build, which we hand to
// Hardhat instead of letting it download a compiler: runs work offline, and a
// contract that compiles in the BUILD tab compiles identically here.

const { subtask } = require('hardhat/config')
const { TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD } = require('hardhat/builtin-tasks/task-names')
require('@nomicfoundation/hardhat-toolbox')

const SOLC = require('solc/package.json').version.split('+')[0]

subtask(TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD, async (args, hre, runSuper) => {
  if (args.solcVersion === SOLC) {
    return {
      compilerPath: require.resolve('solc/soljson.js'),
      isSolcJs: true,
      version: SOLC,
      longVersion: SOLC,
    }
  }
  return runSuper()
})

module.exports = {
  solidity: { version: SOLC, settings: { optimizer: { enabled: true, runs: 200 } } },
  paths: { sources: './contracts', tests: './test', cache: './cache', artifacts: './artifacts' },
  // JSON to a file, not stdout — stdout stays the human-readable transcript.
  mocha: { timeout: 40000, reporter: 'json', reporterOptions: { output: 'mocha.json' } },
}
