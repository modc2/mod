'use client';
/* Chrome around every page + the providers that outlive navigation. */
import { ReactNode, useEffect } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { DataProvider, useData } from '@/lib/data';
import { WalletProvider } from '@/lib/wallet';
import { OverlayProvider } from '@/lib/overlay';
import { ChatProvider, useChat } from '@/lib/chat';
import TopBar from './TopBar';
import Rail from './Rail';
import Overlays from './Overlays';

/* the single-file console routed on #hash — keep those links alive */
const LEGACY_HASH: Record<string, string> = {
  markets: '/markets', traders: '/traders', account: '/account', wallet: '/wallet', trade: '/trade',
  ask: '/chat', chat: '/chat', console: '/console', docs: '/docs', open: '/open', mcp: '/mcp',
};

function LegacyHash() {
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    const h = window.location.hash.slice(1);
    if (path === '/' && LEGACY_HASH[h]) router.replace(LEGACY_HASH[h]);
  }, [path, router]);
  return null;
}

function ScrollTop() {
  const path = usePathname();
  useEffect(() => { window.scrollTo(0, 0); }, [path]);
  return null;
}

function ChatBack() {
  const { showBack } = useChat();
  if (!showBack) return null;
  return <Link id="chat-back" href="/chat">← back to chat</Link>;
}

function Footer() {
  const { info } = useData();
  return (
    <footer>
      bt · the open Bittensor explorer, console &amp; MCP server · network {info?.network || 'finney'}
      {info?.version ? ` · v${info.version}` : ''} · <a href="https://github.com/modc2/mod/tree/main/mod/orbit/bt">source</a>
    </footer>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  return (
    <DataProvider>
      <WalletProvider>
        <OverlayProvider>
          <ChatProvider>
            <LegacyHash />
            <ScrollTop />
            <TopBar />
            <Rail />
            <main>{children}</main>
            <Footer />
            <Overlays />
            <ChatBack />
          </ChatProvider>
        </OverlayProvider>
      </WalletProvider>
    </DataProvider>
  );
}
