"use client";

// /live — a FORWARDER for bookmarks and old links. The LIVE workspace is a
// tab of the STRATS page (/strats?tab=live) since the side panel was removed.
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { stratsHref } from "../lib/stratsNav";

export default function LiveForwarder() {
  const router = useRouter();
  useEffect(() => {
    router.replace(stratsHref("live"));
  }, [router]);
  return null;
}
