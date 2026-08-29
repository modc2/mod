import { NextResponse } from "next/server";
import { fetchImage } from "@/lib/logoClient";

// The uploaded mark's bytes, relayed from the `logo` module so the header's
// <img> stays same-origin — the console keeps working on a deployment where
// only build is exposed to the outside.
//
// Public (it is the mark in everyone's header) and immutable per `?v=` stamp,
// which each save bumps: browsers may cache it hard and still pick up the next
// mark the moment the owner changes it.
export const dynamic = "force-dynamic";

export async function GET() {
  const found = await fetchImage();
  if (!found) {
    return NextResponse.json({ ok: false, error: "no logo image set" }, { status: 404 });
  }
  return new NextResponse(found.bytes, {
    headers: {
      "Content-Type": found.mime,
      "Content-Length": String(found.bytes.byteLength),
      "Cache-Control": "public, max-age=31536000, immutable",
      // An uploaded SVG is markup running from OUR origin if someone opens
      // this URL directly — the relay does not make it ours to trust. Deny it
      // every resource and script it could use, and stop the browser sniffing
      // a different type than the one we declared. (The logo module sets the
      // same headers on its own copy; both hops assert it, so neither one
      // being misconfigured is enough.)
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
      "X-Content-Type-Options": "nosniff",
      "Content-Disposition": "inline",
    },
  });
}
