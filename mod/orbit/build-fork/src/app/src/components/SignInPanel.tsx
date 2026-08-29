"use client";

import { useEffect, useMemo, useState } from "react";
import {
  DEFAULT_NETWORK_ID,
  KEY_TYPES,
  KeyTypeId,
  NETWORK_STORAGE_KEY,
  SIGN_IN_NETWORKS,
  SignInNetwork,
  hasEvmWallet,
  hasPhantom,
  hasSubstrateWallet,
  networkById,
  shortAddress,
  substrateWalletName,
} from "../utils/keytypes";
import { NETWORK_LOGOS } from "../utils/wallet";

/** Every way in, in the order a first-time visitor should meet them. */
export type SignInMethod = "instant" | "metamask" | "subwallet" | "phantom" | "polkadot" | "password";

interface SignInPanelProps {
  apiUrl: string;
  authLoading: boolean;
  authError: string | null;
  /** Address of the key already in this browser, if there is one. */
  localSeedAddr: string | null;
  onClose: () => void;
  /** Each way in carries the chain the signer picked, so an EVM session lands
      on that network instead of wherever the wallet happened to be. */
  onInstant: (chainId?: number) => void;
  onWallet: (method: Exclude<SignInMethod, "instant" | "password">, chainId?: number) => void;
  onPassword: (password: string, chainId?: number) => void;
}

interface WalletOption {
  method: Exclude<SignInMethod, "instant" | "password">;
  name: string;
  keyType: KeyTypeId;
  /** Where to get it, for the ones that aren't installed. */
  install: string;
  installed: boolean;
  /** Line-art mark — an emoji renders as tofu wherever the font is missing. */
  icon: JSX.Element;
}

const mark = (d: string) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
);

/** Marks for the chains that aren't EVM, so have no NETWORK_LOGOS entry. */
const CHAIN_GLYPHS: Record<string, string> = {
  solana: `<path d="M6 7h13l-3 3H3zM5 12h13l-3 3H2zM6 17h13l-3 3H3z" fill="currentColor" opacity="0.85"/>`,
  polkadot: `<ellipse cx="12" cy="5.5" rx="4" ry="2.4" fill="currentColor"/><ellipse cx="12" cy="18.5" rx="4" ry="2.4" fill="currentColor"/><ellipse cx="6.4" cy="8.8" rx="4" ry="2.4" fill="currentColor" opacity="0.6" transform="rotate(-60 6.4 8.8)"/><ellipse cx="17.6" cy="15.2" rx="4" ry="2.4" fill="currentColor" opacity="0.6" transform="rotate(-60 17.6 15.2)"/><ellipse cx="17.6" cy="8.8" rx="4" ry="2.4" fill="currentColor" opacity="0.6" transform="rotate(60 17.6 8.8)"/><ellipse cx="6.4" cy="15.2" rx="4" ry="2.4" fill="currentColor" opacity="0.6" transform="rotate(60 6.4 15.2)"/>`,
  bittensor: `<path d="M4 6h16v2.4h-6.8V20h-2.4V8.4H4z" fill="currentColor"/>`,
};

/** A chain's mark: its own logo where we have one, its family's otherwise. */
function NetworkGlyph({ net, size = 16 }: { net: SignInNetwork; size?: number }) {
  const svg = (net.chainId && NETWORK_LOGOS[net.chainId]?.svg) || CHAIN_GLYPHS[net.id]
    || '<circle cx="12" cy="12" r="8" fill="currentColor" opacity="0.35"/>';
  return (
    <span
      className="shrink-0 flex items-center justify-center"
      style={{ width: size, height: size, color: net.color }}
      dangerouslySetInnerHTML={{ __html: `<svg viewBox="0 0 24 24" width="${size}" height="${size}">${svg}</svg>` }}
    />
  );
}

/** A small pill naming the curve, coloured by family. */
export function KeyTypeBadge({ keyType, size = 9 }: { keyType: KeyTypeId; size?: number }) {
  const info = KEY_TYPES[keyType];
  return (
    <span
      title={`${info.label} key · ${info.family} · addresses look like ${info.addressFormat}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "1px 6px",
        borderRadius: 999,
        fontSize: size,
        fontWeight: 700,
        letterSpacing: "0.06em",
        fontFamily: "'JetBrains Mono', monospace",
        color: info.color,
        background: `color-mix(in srgb, ${info.color} 12%, transparent)`,
        border: `1px solid color-mix(in srgb, ${info.color} 30%, transparent)`,
        whiteSpace: "nowrap",
      }}
    >
      {info.label}
    </span>
  );
}

/** The chains a key of this type can act on. */
export function NetworkList({ keyType, max = 4 }: { keyType: KeyTypeId; max?: number }) {
  const info = KEY_TYPES[keyType];
  const shown = info.networks.slice(0, max);
  const rest = info.networks.length - shown.length;
  return (
    <span
      className="text-[10px]"
      style={{ color: "var(--text-tertiary)" }}
      title={`Compatible networks: ${info.networks.join(", ")}`}
    >
      {shown.join(" · ")}
      {rest > 0 ? ` +${rest}` : ""}
    </span>
  );
}

/** One way in. Every method is a row of this shape — none of them is a hero
    button, because none of them is the "right" one to pick for a signer. */
function MethodRow({
  keyType,
  icon,
  name,
  hint,
  right,
  disabled,
  onClick,
}: {
  keyType: KeyTypeId;
  icon: JSX.Element;
  name: string;
  hint: JSX.Element | string;
  right?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  const color = KEY_TYPES[keyType].color;
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="flex items-center gap-3 px-3 py-2.5 text-left transition-all focus-ring w-full"
      style={{
        border: `1px solid color-mix(in srgb, ${color} 22%, var(--border-color))`,
        background: "var(--bg-primary)",
        borderRadius: 12,
        opacity: disabled ? 0.6 : 1,
      }}
      onMouseEnter={(e) => {
        if (disabled) return;
        e.currentTarget.style.background = `color-mix(in srgb, ${color} 6%, var(--bg-primary))`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "var(--bg-primary)";
      }}
    >
      <span
        className="shrink-0 flex items-center justify-center"
        style={{
          width: 30, height: 30, borderRadius: 9,
          color,
          background: "var(--bg-secondary)",
          border: "1px solid var(--border-color)",
        }}
      >
        {icon}
      </span>
      <span className="flex-1 min-w-0">
        <span className="flex items-center gap-2">
          <span className="text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>
            {name}
          </span>
          <KeyTypeBadge keyType={keyType} />
        </span>
        <span className="block mt-0.5 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
          {hint}
        </span>
      </span>
      <span className="shrink-0 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
        {right ?? "›"}
      </span>
    </button>
  );
}

export default function SignInPanel({
  apiUrl,
  authLoading,
  authError,
  localSeedAddr,
  onClose,
  onInstant,
  onWallet,
  onPassword,
}: SignInPanelProps) {
  const [showPassword, setShowPassword] = useState(false);
  const [password, setPassword] = useState("");
  const [detected, setDetected] = useState(0); // bump to re-run detection
  /** Curves the server will actually verify — the UI never offers a key the
      backend can't check. Falls back to all three if the probe fails. */
  const [serverKeyTypes, setServerKeyTypes] = useState<KeyTypeId[] | null>(null);
  /** The chain you're here to act on. Ethereum until told otherwise; the last
      choice is remembered so a Base user isn't re-picking Base every visit. */
  const [networkId, setNetworkId] = useState<string>(DEFAULT_NETWORK_ID);
  const [showTestnets, setShowTestnets] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(NETWORK_STORAGE_KEY);
      const net = networkById(saved);
      if (net) {
        setNetworkId(net.id);
        if (net.testnet) setShowTestnets(true);
      }
    } catch { /* private mode — Ethereum it is */ }
  }, []);

  useEffect(() => {
    let alive = true;
    fetch(`${apiUrl}/auth/key-types`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!alive || !d?.key_types) return;
        setServerKeyTypes(d.key_types.map((k: any) => k.id).filter((id: string) => id in KEY_TYPES));
      })
      .catch(() => { /* older API — assume the full set */ });
    return () => { alive = false; };
  }, [apiUrl]);

  // Extensions inject asynchronously; re-check for a beat after mount so a
  // wallet that loads late still shows up without a manual reload.
  useEffect(() => {
    const timers = [150, 600, 1500].map((ms) => setTimeout(() => setDetected((n) => n + 1), ms));
    return () => timers.forEach(clearTimeout);
  }, []);

  const wallets: WalletOption[] = useMemo(() => {
    if (typeof window === "undefined") return [];
    const substrateName = substrateWalletName();
    return [
      {
        method: "metamask" as const,
        name: "MetaMask",
        keyType: "secp256k1" as KeyTypeId,
        install: "metamask.io",
        installed: hasEvmWallet("metamask"),
        icon: mark("M3 7a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM16 12h4"),
      },
      {
        method: "phantom" as const,
        name: "Phantom",
        keyType: "ed25519" as KeyTypeId,
        install: "phantom.app",
        installed: hasPhantom(),
        icon: mark("M4 14a8 8 0 0 1 16 0v4a2 2 0 0 1-3.5 1.3A2 2 0 0 1 13 19a2 2 0 0 1-3.5 1.3A2 2 0 0 1 6 19a2 2 0 0 1-2-2zM9 12h.01M15 12h.01"),
      },
      {
        method: "polkadot" as const,
        name: substrateName || "SubWallet",
        keyType: "sr25519" as KeyTypeId,
        install: "subwallet.app",
        installed: hasSubstrateWallet(),
        icon: mark("M12 3l8 9-8 9-8-9z"),
      },
      {
        method: "subwallet" as const,
        name: "SubWallet (EVM)",
        keyType: "secp256k1" as KeyTypeId,
        install: "subwallet.app",
        installed: hasEvmWallet("subwallet"),
        icon: mark("M3 7a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 3l4 4-4 4-4-4z"),
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detected]);

  // Networks the server can actually verify a signature for.
  const networks = useMemo(
    () => SIGN_IN_NETWORKS.filter((n) => !serverKeyTypes || serverKeyTypes.includes(n.keyType)),
    [serverKeyTypes],
  );

  // The selected chain, and the curve it implies. If the server turns out not
  // to verify the remembered network's curve, fall back to the first it does.
  const network = networkById(networkId) && networks.some((n) => n.id === networkId)
    ? (networkById(networkId) as SignInNetwork)
    : networks[0] || SIGN_IN_NETWORKS[0];
  const keyType = network.keyType;

  const chooseNetwork = (net: SignInNetwork) => {
    setNetworkId(net.id);
    try { localStorage.setItem(NETWORK_STORAGE_KEY, net.id); } catch { /* private mode */ }
  };

  // Only the wallets that can hold a key for the chosen chain. Installed ones
  // first, then the ones you'd have to go get.
  const forChain = wallets.filter((w) => w.keyType === keyType);
  const installed = forChain.filter((w) => w.installed);
  const missing = forChain.filter((w) => !w.installed);
  // The password / in-browser keys are derived with ethers — secp256k1 only.
  const showDerivedKeys = keyType === "secp256k1";

  const keyIcon = mark("M15 7a4 4 0 1 1-3.9 5H7v3H4v-3l3.1-3H11A4 4 0 0 1 15 7z");
  const lockIcon = mark("M7 11V8a5 5 0 0 1 10 0v3M5 11h14v9H5z");

  return (
    <div className="h-full flex flex-col overflow-y-auto" style={{ background: "var(--bg-secondary)" }}>
      <div className="flex-1 flex flex-col justify-center p-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 mb-1">
          <h2 className="text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>
            Sign in
          </h2>
          <button
            onClick={onClose}
            className="text-[18px] leading-none px-2 py-1"
            style={{ color: "var(--text-tertiary)" }}
            title="Hide — browse the hub"
          >
            ×
          </button>
        </div>
        <div className="text-[13px] mb-4 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
          Your key is your account. Pick the chain you&apos;re here for, sign one
          message, you&apos;re in.
        </div>

        {/* ── Network first ──────────────────────────────────────────────
            The chain decides the curve, and the curve decides which wallets
            can sign — so this row is the first choice, not a setting buried
            after the fact. Ethereum is selected until someone says otherwise. */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[9px] tracking-[2px]" style={{ color: "var(--text-tertiary)" }}>
              NETWORK
            </span>
            <button
              onClick={() => setShowTestnets((v) => !v)}
              className="text-[9px] tracking-wider px-2 py-0.5"
              style={{
                color: showTestnets ? network.color : "var(--text-tertiary)",
                border: "1px solid var(--border-color)",
                borderRadius: 8,
              }}
            >
              {showTestnets ? "HIDE TESTNETS" : "TESTNETS"}
            </button>
          </div>
          <div className="grid grid-cols-4 gap-1.5">
            {networks
              .filter((n) => showTestnets || !n.testnet)
              .map((n) => {
                const active = n.id === network.id;
                return (
                  <button
                    key={n.id}
                    onClick={() => chooseNetwork(n)}
                    title={`${n.name} · signs with a ${KEY_TYPES[n.keyType].label} key`}
                    aria-pressed={active}
                    className="flex flex-col items-center gap-1 py-2 px-1 transition-all focus-ring"
                    style={{
                      border: active
                        ? `1px solid color-mix(in srgb, ${n.color} 45%, transparent)`
                        : "1px solid var(--border-color)",
                      background: active
                        ? `color-mix(in srgb, ${n.color} 10%, transparent)`
                        : "var(--bg-primary)",
                      borderRadius: 10,
                    }}
                  >
                    <NetworkGlyph net={n} size={18} />
                    <span
                      className="text-[9px] font-semibold tracking-wide text-center leading-tight"
                      style={{ color: active ? n.color : "var(--text-tertiary)" }}
                    >
                      {n.name}
                    </span>
                  </button>
                );
              })}
          </div>
          <div className="flex items-center gap-2 mt-2 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            <span>{network.name} signs with a</span>
            <KeyTypeBadge keyType={keyType} />
            <span>key</span>
          </div>
        </div>

        {/* ── Every way in on that curve, laid out flat ──────────────── */}
        <div className="flex flex-col gap-2">
          {installed.map((w) => (
            <MethodRow
              key={w.method}
              keyType={w.keyType}
              icon={w.icon}
              name={w.name}
              hint={<NetworkList keyType={w.keyType} />}
              disabled={authLoading}
              onClick={() => onWallet(w.method, network.chainId)}
            />
          ))}

          {showDerivedKeys && (
          <MethodRow
            keyType="secp256k1"
            icon={lockIcon}
            name="Password key"
            hint="The same password always derives the same key — carries to any browser"
            right={showPassword ? "−" : "+"}
            disabled={authLoading}
            onClick={() => setShowPassword((v) => !v)}
          />
          )}

          {showDerivedKeys && showPassword && (
            <div className="w-full space-y-2 pb-1">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password..."
                autoFocus
                className="w-full px-3 py-2 text-[13px] bg-crt-dark text-crt-green border-2 border-crt-amber/40 font-pixel"
                style={{ letterSpacing: "0.01em" }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && password.trim()) onPassword(password.trim(), network.chainId);
                }}
              />
              <button
                onClick={() => password.trim() && onPassword(password.trim(), network.chainId)}
                disabled={authLoading || !password.trim()}
                className="pixel-btn pixel-btn-amber w-full text-[13px] py-3"
                style={{ letterSpacing: "0.04em" }}
              >
                {authLoading ? <span className="animate-pulse">DERIVING KEY...</span> : "Continue with password"}
              </button>
            </div>
          )}

          {showDerivedKeys && (
          <MethodRow
            keyType="secp256k1"
            icon={keyIcon}
            name={localSeedAddr ? `Key in this browser · ${shortAddress(localSeedAddr)}` : "New key in this browser"}
            hint={
              localSeedAddr
                ? "Same key as last time — it never leaves this browser"
                : "Generated here, kept here. Nothing to install, nothing to remember"
            }
            disabled={authLoading}
            onClick={() => onInstant(network.chainId)}
          />
          )}

          {/* Nothing on this curve is installed and none of it can be derived
              here — say so rather than showing an empty panel. */}
          {installed.length === 0 && !showDerivedKeys && (
            <div
              className="px-3 py-3 text-[11px] leading-relaxed"
              style={{
                border: `1px solid color-mix(in srgb, ${network.color} 22%, var(--border-color))`,
                background: `color-mix(in srgb, ${network.color} 5%, transparent)`,
                borderRadius: 12,
                color: "var(--text-tertiary)",
              }}
            >
              No {KEY_TYPES[keyType].family} wallet detected in this browser.
              Install one below, or pick an EVM network to sign in with a key
              derived right here.
            </div>
          )}

          {authLoading && (
            <div className="text-[11px] text-center animate-pulse pt-1" style={{ color: "var(--text-tertiary)" }}>
              SIGNING IN...
            </div>
          )}

          {missing.length > 0 && (
            <div className="text-[11px] leading-relaxed pt-2" style={{ color: "var(--text-tertiary)" }}>
              Not installed in this browser:{" "}
              {missing.map((w, i) => (
                <span key={w.method}>
                  {i > 0 && ", "}
                  <span title={`${KEY_TYPES[w.keyType].label} · ${KEY_TYPES[w.keyType].networks.join(", ")}`}>
                    {w.name} ({w.install})
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center justify-center pt-4 text-[12px]">
          <button
            onClick={onClose}
            className="underline underline-offset-2"
            style={{ color: "var(--text-tertiary)" }}
            title="Browse the hub read-only — signing in only matters when you want to edit"
          >
            Just browsing
          </button>
        </div>

        {authError && (
          <div className="mt-4 border-2 border-crt-red/60 p-3" style={{ background: "rgba(239,68,68,0.05)" }}>
            <div className="text-[14px] text-crt-red text-center">{authError}</div>
          </div>
        )}
      </div>

      <div className="p-4 text-[11px] text-center leading-relaxed" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
        However you sign in, you sign one challenge and the server verifies it
        on your key&apos;s own curve — secp256k1, ed25519 or sr25519. Keys and
        passwords stay viewable in ACCOUNT.
      </div>
    </div>
  );
}
