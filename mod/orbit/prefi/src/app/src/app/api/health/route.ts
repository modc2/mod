import { NextResponse } from 'next/server'

export async function GET() {
  const health = {
    status: 'healthy',
    timestamp: new Date().toISOString(),
    service: 'prefi-prediction-market',
    network: 'base-mainnet',
    version: '1.0.0',
    environment: process.env.NODE_ENV || 'development',
    chain: {
      id: process.env.NEXT_PUBLIC_CHAIN_ID || '8453',
      name: 'Base',
      rpc: process.env.NEXT_PUBLIC_BASE_RPC || 'https://mainnet.base.org',
    },
    contracts: {
      predictionMarket: process.env.NEXT_PUBLIC_PREDICTION_MARKET_ADDRESS || 'not-deployed',
      oracle: process.env.NEXT_PUBLIC_ORACLE_ADDRESS || 'not-deployed',
      collateralToken: process.env.NEXT_PUBLIC_COLLATERAL_TOKEN || 'not-deployed',
    },
    uniswap: {
      ethUsdcPool: process.env.NEXT_PUBLIC_UNISWAP_V3_ETH_USDC_POOL || 'not-configured',
    }
  }

  return NextResponse.json(health, {
    status: 200,
    headers: {
      'Cache-Control': 'no-store, max-age=0',
    }
  })
}
