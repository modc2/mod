import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

// Resolve the deployed contracts' Solidity source. The app bundles the
// compiled Hardhat artifacts under app/contracts/<area>/<Name>.sol/<Name>.json,
// each of which records the `sourceName` (path relative to the chain project
// root). The actual .sol source lives in the chain project at
// mod/core/chain/src/<sourceName>. We read the artifact for the source path,
// then return the source text — so the Contracts page can show the exact code
// behind the already-deployed addresses without duplicating the .sol files.
const MOD_BASE = path.join(process.env.HOME || '', 'mod', 'mod')
const ARTIFACTS = path.join(MOD_BASE, 'core', 'app', 'app', 'contracts')
const CHAIN_SRC = path.join(MOD_BASE, 'core', 'chain', 'src')

// Contract display name → compiled-artifact path (mirrors the ABI imports in
// app/contracts/page.tsx). The token variants (USDC/USDT/NativeToken) all
// share Token.sol.
const ARTIFACT_PATH: Record<string, string> = {
  Treasury: 'treasury/Treasury.sol/Treasury.json',
  Market: 'market/Market.sol/Market.json',
  Debit: 'market/debit/Debit.sol/Debit.json',
  Registry: 'registry/Registry.sol/Registry.json',
  ManualPriceOracle: 'oracles/ManualPriceOracle.sol/ManualPriceOracle.json',
  TokenGate: 'tokengate/TokenGate.sol/TokenGate.json',
  BlocTime: 'bloctime/BlocTime.sol/BlocTime.json',
  NativeToken: 'token/Token.sol/Token.json',
  USDC: 'token/Token.sol/Token.json',
  USDT: 'token/Token.sol/Token.json',
}

/**
 * GET /api/contract-source        → list contracts with available source
 * GET /api/contract-source?name=X → { name, sourceName, code }
 */
export async function GET(request: NextRequest) {
  const name = request.nextUrl.searchParams.get('name')

  if (!name) {
    return NextResponse.json({ contracts: Object.keys(ARTIFACT_PATH) })
  }

  const rel = ARTIFACT_PATH[name]
  if (!rel) {
    return NextResponse.json({ error: `No source mapping for "${name}"` }, { status: 404 })
  }

  try {
    const artifact = JSON.parse(fs.readFileSync(path.join(ARTIFACTS, rel), 'utf-8'))
    const sourceName: string = artifact.sourceName
    if (!sourceName) {
      return NextResponse.json({ error: 'Artifact missing sourceName' }, { status: 404 })
    }
    // Resolve + guard against path traversal escaping the chain source root.
    const srcPath = path.resolve(CHAIN_SRC, sourceName)
    if (!srcPath.startsWith(CHAIN_SRC)) {
      return NextResponse.json({ error: 'Invalid source path' }, { status: 400 })
    }
    const code = fs.readFileSync(srcPath, 'utf-8')
    return NextResponse.json({ name, sourceName, code })
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || 'Source not found' }, { status: 404 })
  }
}
