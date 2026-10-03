import { NextRequest } from 'next/server'
import { proxyToChainApi } from '../../../chain/apiProxy'

/**
 * /api/chain/* → the chain module's API.
 *
 * Kept for anything still pointed here (it only works when you hit the app
 * directly — the fleet gateway claims /api/*). The console itself calls
 * /chain/api/* instead; see app/chain/api/[...path]/route.ts.
 */

type Ctx = { params: { path: string[] } }

export const dynamic = 'force-dynamic'

export const GET = (req: NextRequest, { params }: Ctx) => proxyToChainApi(req, params.path)
export const POST = (req: NextRequest, { params }: Ctx) => proxyToChainApi(req, params.path)
export const DELETE = (req: NextRequest, { params }: Ctx) => proxyToChainApi(req, params.path)
