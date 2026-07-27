// "Log in with Claude" — the OAuth PKCE flow that mints a working
// ~/.claude/.credentials.json without cat/pasting one from another machine.
//
// This app runs on a remote host reached through a browser, so the loopback
// (http://localhost:PORT/callback) variant the desktop CLI uses doesn't apply:
// claude.ai would redirect to localhost on the *operator's* machine, not ours.
// We therefore use the MANUAL code flow — claude.ai shows the owner a code on
// the redirect page, they paste it back here, and we exchange it server-side.
//
// Flow:
//   1. buildAuthorize()  → {url, verifier, state}. Owner opens `url`, approves.
//   2. claude.ai redirects to REDIRECT_URI which displays `code#state`.
//   3. Owner pastes that string back; exchangeCode() swaps it for tokens.
//   4. writeCredentials() persists them where the CLI reads them.
//
// The endpoints/client_id below are Claude Code's own OAuth client. They are
// NOT formally documented and Anthropic is mid-rebrand (console.anthropic.com →
// platform.claude.com), so every value is overridable via env without a
// redeploy. The defaults are the battle-tested values used by the widely-run
// open-source reimplementations (opencode, claude-code-login).
import fs from "fs";
import os from "os";
import path from "path";
import crypto from "crypto";

const env = (k: string, d: string) => process.env[k]?.trim() || d;

export const OAUTH = {
  clientId: env("CLAUDE_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e"),
  authorizeUrl: env("CLAUDE_OAUTH_AUTHORIZE_URL", "https://claude.ai/oauth/authorize"),
  tokenUrl: env("CLAUDE_OAUTH_TOKEN_URL", "https://console.anthropic.com/v1/oauth/token"),
  redirectUri: env("CLAUDE_OAUTH_REDIRECT_URI", "https://console.anthropic.com/oauth/code/callback"),
  scope: env("CLAUDE_OAUTH_SCOPE", "org:create_api_key user:profile user:inference"),
};

export const CRED_PATH = path.join(os.homedir(), ".claude", ".credentials.json");

function b64url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// PKCE S256: verifier is a high-entropy random string, challenge = its SHA-256.
export function buildAuthorize(): { url: string; verifier: string; state: string } {
  const verifier = b64url(crypto.randomBytes(32));
  const challenge = b64url(crypto.createHash("sha256").update(verifier).digest());
  const state = b64url(crypto.randomBytes(32));
  const q = new URLSearchParams({
    code: "true", // Claude Code sets this; claude.ai uses it to render the paste page.
    client_id: OAUTH.clientId,
    response_type: "code",
    redirect_uri: OAUTH.redirectUri,
    scope: OAUTH.scope,
    code_challenge: challenge,
    code_challenge_method: "S256",
    state,
  });
  return { url: `${OAUTH.authorizeUrl}?${q.toString()}`, verifier, state };
}

export interface OAuthCreds {
  accessToken: string;
  refreshToken?: string;
  expiresAt: number;
  scopes: string[];
  subscriptionType: string;
  rateLimitTier?: string;
}

// Exchange the pasted `code#state` for tokens. The manual redirect page hands
// back the auth code with the state appended after a `#`; split it off and
// verify it matches the state we generated (CSRF guard).
export async function exchangeCode(
  pasted: string,
  verifier: string,
  expectedState: string
): Promise<OAuthCreds> {
  const raw = pasted.trim();
  if (!raw) throw new Error("no code provided");
  const [code, stateFromCode] = raw.split("#");
  if (!code) throw new Error("could not parse code");
  if (stateFromCode && stateFromCode !== expectedState) {
    throw new Error("state mismatch — start the login again");
  }

  const res = await fetch(OAUTH.tokenUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      grant_type: "authorization_code",
      code,
      state: stateFromCode || expectedState,
      client_id: OAUTH.clientId,
      redirect_uri: OAUTH.redirectUri,
      code_verifier: verifier,
    }),
  });

  const text = await res.text();
  if (!res.ok) {
    // Surface the provider's raw error — these endpoints are undocumented, so a
    // verbatim message is the fastest way to diagnose a rebrand/param change.
    throw new Error(`token exchange failed (${res.status}): ${text.slice(0, 300)}`);
  }
  let data: any;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`token endpoint returned non-JSON: ${text.slice(0, 200)}`);
  }
  if (!data?.access_token) {
    throw new Error(`token response missing access_token: ${text.slice(0, 200)}`);
  }

  const expiresIn = Number(data.expires_in) || 8 * 3600;
  const scope: string = data.scope || OAUTH.scope;
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token || undefined,
    expiresAt: Date.now() + expiresIn * 1000,
    scopes: scope.split(/\s+/).filter(Boolean),
    subscriptionType: data.subscription_type || data.subscriptionType || "max",
    rateLimitTier: data.rate_limit_tier || data.rateLimitTier || undefined,
  };
}

// Persist credentials where the CLI reads them. Mirrors the paste route's write:
// replace the entrypoint's host symlink with a real node-owned 0600 file so the
// new token takes effect immediately without clobbering the host's copy.
export function writeCredentials(oauth: OAuthCreds): void {
  const payload = { claudeAiOauth: oauth };
  const dir = path.dirname(CRED_PATH);
  fs.mkdirSync(dir, { recursive: true });
  try {
    if (fs.lstatSync(CRED_PATH).isSymbolicLink()) fs.unlinkSync(CRED_PATH);
  } catch {}
  fs.writeFileSync(CRED_PATH, JSON.stringify(payload, null, 2), { mode: 0o600 });
}
