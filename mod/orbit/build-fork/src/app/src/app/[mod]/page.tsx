// /build-fork/{mod} — the console, addressed at a module.
//
// It is the SAME page component as /build-fork, deliberately: the console reads
// the module out of window.location and moves its own address with
// history.pushState, so switching modules never remounts this tree. This file
// exists so that a /build-fork/{mod} URL is a real, refreshable, linkable route
// instead of a 404 — the dynamic segment itself is read client-side.
//
// Reserved segments (api, auth, _next) are static routes and win over this
// one; modules with those names address as /build-fork?mod={name}. See modHref().
export { default } from "../page";
