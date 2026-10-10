import { NextRequest } from 'next/server'
import { proxyToChainApi } from '../../apiProxy'

/**
 * /chain/api/* → the chain module's API.
 *
 * This bridge lives UNDER the page path rather than under /api/* on purpose:
 * the fleet gateway on :3000 hands every /api/* request straight to the
 * protocol API on :8000 and Next never sees it, so a bridge mounted at
 * /api/chain answers when you hit :3001 directly and 405s on the public host —
 * which left the console with no templates, no projects and no contracts.
 * /chain/* reaches the app both ways.
 */

type Ctx = { params: { path: string[] } }

export const dynamic = 'force-dynamic'

export const GET = (req: NextRequest, { params }: Ctx) => proxyToChainApi(req, params.path)
export const POST = (req: NextRequest, { params }: Ctx) => proxyToChainApi(req, params.path)
export const DELETE = (req: NextRequest, { params }: Ctx) => proxyToChainApi(req, params.path)
