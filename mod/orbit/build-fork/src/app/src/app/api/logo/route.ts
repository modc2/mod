import { NextRequest, NextResponse } from "next/server";
import { isOwnerRequest, localMode, OWNER_ONLY } from "@/lib/ownerAuth";
import { fetchLogo, fetchOwner, saveLogo, LOGO_API, LOGO_MODULE } from "@/lib/logoClient";

// The header mark, which lives in the `logo` module now — this route is the
// seam between the two.
//
// GET is public and never fails: everyone who loads the console sees the same
// mark, and a console that won't render because another module is asleep is
// worse than a console showing yesterday's cube.
//
// POST carries TWO different proofs, and needs both:
//
//   Authorization: Bearer <build token>   you are signed into THIS console as
//                                         the owner. Keeps the door shut here.
//   x-mod-token: <mod-protocol token>     the owner's wallet signature, which
//                                         the logo module verifies against
//                                         build's own config.json `owner`.
//
// Only the second one actually authorizes the write, and this process cannot
// produce it. Build runs as root and forwards the signature; it does not hold
// the authority. That is the whole reason the mark moved out of here.
export const dynamic = "force-dynamic";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/build-fork";

export async function GET() {
  const { logo, source } = await fetchLogo(BASE_PATH);
  const owner = await fetchOwner();
  return NextResponse.json(
    {
      ok: true,
      logo,
      // Where the answer came from, and who may change it. The panel reads
      // both: "stale" and "not your key" are different problems and the owner
      // should not have to guess which one they have.
      source,
      module: LOGO_MODULE,
      api: LOGO_API,
      owner: owner?.owner ?? null,
      owners: owner?.addresses ?? [],
      // A save needs a wallet signature — always, including here. Said out
      // loud so the panel can offer the CLI instead of a form that can only fail.
      signature_required: !localMode(),
      cli: `m logo/glyph ${LOGO_MODULE.split("/").pop()} <glyph>`,
    },
    { headers: { "Cache-Control": "no-store" } }
  );
}

export async function POST(req: NextRequest) {
  // Door one: are you the owner of this console?
  if (!isOwnerRequest(req)) return NextResponse.json(OWNER_ONLY, { status: 401 });

  // Door two: the signature the logo module will actually check.
  const modToken = req.headers.get("x-mod-token")?.trim();
  if (!modToken) {
    return NextResponse.json(
      {
        ok: false,
        error:
          "the logo module needs a wallet signature — connect a wallet and try again, " +
          `or set it on the host with \`m logo/glyph ${LOGO_MODULE.split("/").pop()} <glyph>\``,
      },
      { status: 401 }
    );
  }

  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "expected a JSON body" }, { status: 400 });
  }

  const result = await saveLogo(body ?? {}, modToken, BASE_PATH);
  if (!result.ok) {
    return NextResponse.json({ ok: false, error: result.error }, { status: result.status });
  }
  return NextResponse.json({ ok: true, logo: result.logo, by: result.by });
}
