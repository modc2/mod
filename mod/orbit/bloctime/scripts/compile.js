#!/usr/bin/env node
/**
 * Compile Solidity source with the module's own solc.
 *
 * Reads {source, filename, optimize, runs} as JSON on stdin, writes solc's
 * standard-JSON output on stdout. Imports resolve against node_modules
 * (so `@openzeppelin/...` works out of the box) and contracts/, which is
 * what the checked-in BlocTime.sol / NativeToken.sol already rely on.
 */

const fs = require('fs');
const path = require('path');
const solc = require('solc');

const MODULE_DIR = path.resolve(__dirname, '..');
const ROOTS = [path.join(MODULE_DIR, 'contracts'), path.join(MODULE_DIR, 'node_modules')];

function findImport(importPath) {
  const candidates = ROOTS.map(root => path.join(root, importPath));
  // Bare relative imports also resolve next to the contracts dir.
  for (const file of candidates) {
    // Keep resolution inside the module — no walking out with ../../
    if (!ROOTS.some(root => file.startsWith(root + path.sep))) continue;
    try {
      return { contents: fs.readFileSync(file, 'utf8') };
    } catch (e) { /* try the next root */ }
  }
  return { error: `File not found: ${importPath}` };
}

function main() {
  const req = JSON.parse(fs.readFileSync(0, 'utf8'));
  const filename = req.filename || 'Contract.sol';
  const input = {
    language: 'Solidity',
    sources: { [filename]: { content: req.source || '' } },
    settings: {
      optimizer: { enabled: req.optimize !== false, runs: req.runs || 200 },
      outputSelection: {
        '*': { '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object', 'metadata'] },
      },
    },
  };
  if (req.evmVersion) input.settings.evmVersion = req.evmVersion;

  const output = solc.compile(JSON.stringify(input), { import: findImport });
  process.stdout.write(JSON.stringify({ version: solc.version(), output: JSON.parse(output) }));
}

main();
