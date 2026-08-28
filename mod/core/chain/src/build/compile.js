#!/usr/bin/env node
// Solidity compiler shim for the contract builder.
//
// Reads {sources: {file: source}, optimize, runs, evmVersion} as JSON on stdin,
// runs solc's standard-JSON compiler, prints the raw solc output on stdout.
// Imports resolve against the chain module's node_modules (so @openzeppelin/…
// works out of the box) — nothing outside it is readable.

const fs = require('fs')
const path = require('path')
const solc = require('solc')

const MODULES = path.resolve(__dirname, '..', '..', 'node_modules')

// Uploaded projects were laid out for whatever toolchain built them. Foundry
// vendors libraries under lib/, hardhat expects them in node_modules, and both
// habits show up in the same imports — so rewrite the well-known prefixes onto
// the packages this module actually ships.
const REMAPPINGS = [
  ['lib/openzeppelin-contracts-upgradeable/contracts/', '@openzeppelin/contracts-upgradeable-4.7.3/'],
  ['@openzeppelin/contracts-upgradeable/', '@openzeppelin/contracts-upgradeable-4.7.3/'],
  ['lib/openzeppelin-contracts/contracts/', '@openzeppelin/contracts/'],
  ['openzeppelin-contracts/contracts/', '@openzeppelin/contracts/'],
  ['lib/chainlink/contracts/src/', '@chainlink/contracts/src/'],
]

/** True if `dir` holds a .sol file within its top two levels. */
function hasSolidity(dir, depth = 3) {
  let entries
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return false
  }
  for (const entry of entries) {
    if (entry.isFile() && entry.name.endsWith('.sol')) return true
    if (entry.isDirectory() && depth > 1 && hasSolidity(path.join(dir, entry.name), depth - 1)) {
      return true
    }
  }
  return false
}

/** Installed Solidity packages, for the "what can I import?" error message. */
function installed() {
  const names = []
  for (const entry of fs.readdirSync(MODULES, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    if (entry.name.startsWith('@')) {
      for (const sub of fs.readdirSync(path.join(MODULES, entry.name))) {
        if (hasSolidity(path.join(MODULES, entry.name, sub))) names.push(`${entry.name}/${sub}`)
      }
    } else if (hasSolidity(path.join(MODULES, entry.name))) {
      names.push(entry.name)
    }
  }
  return names.sort()
}

function readPackage(importPath) {
  // A resolved path must stay inside node_modules — nothing else is readable.
  const full = path.resolve(MODULES, importPath)
  if (!full.startsWith(MODULES + path.sep)) return null
  try {
    return fs.readFileSync(full, 'utf8')
  } catch {
    return null
  }
}

function makeFindImport(sources) {
  const byName = new Map()   // basename -> the one project file that has it
  for (const file of Object.keys(sources)) {
    const base = path.posix.basename(file)
    byName.set(base, byName.has(base) ? null : file)   // null marks "ambiguous"
  }

  return function findImport(importPath) {
    const clean = importPath.replace(/^\.\//, '')

    // 1) the project's own files — an upload keeps whatever layout it had, so
    //    `src/Vault.sol` and a bare `Vault.sol` should both find the file.
    for (const key of [importPath, clean, byName.get(path.posix.basename(clean))]) {
      if (key && sources[key]) return { contents: sources[key].content }
    }

    // 2) an installed package, directly or through a remapping
    const direct = readPackage(clean)
    if (direct !== null) return { contents: direct }
    for (const [from, to] of REMAPPINGS) {
      if (!clean.startsWith(from)) continue
      const mapped = readPackage(to + clean.slice(from.length))
      if (mapped !== null) return { contents: mapped }
    }

    return {
      error: `File not found: ${importPath}\n`
        + `Not in this project, and not an installed library. Available: ${installed().join(', ')}`,
    }
  }
}

function main() {
  const raw = fs.readFileSync(0, 'utf8')
  const req = JSON.parse(raw || '{}')
  const sources = {}
  for (const [file, content] of Object.entries(req.sources || {})) {
    sources[file] = { content }
  }

  const input = {
    language: 'Solidity',
    sources,
    settings: {
      optimizer: { enabled: req.optimize !== false, runs: req.runs || 200 },
      evmVersion: req.evmVersion || undefined,
      outputSelection: {
        '*': { '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object'] },
      },
    },
  }

  const out = solc.compile(JSON.stringify(input), { import: makeFindImport(sources) })
  process.stdout.write(out)
}

try {
  main()
} catch (e) {
  process.stdout.write(JSON.stringify({
    errors: [{ severity: 'error', formattedMessage: String(e && e.message || e) }],
  }))
  process.exitCode = 1
}
