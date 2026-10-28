'use client';
/* Chat — the full-page host for components/ChatThread. The run itself lives
 * in the layout, so leaving this page mid-answer does not stop it (the
 * ChatDock picks it up on every other page). */
import { useChat } from '@/lib/chat';
import { Section } from '@/components/ui';
import ChatThread from '@/components/ChatThread';

export default function Chat() {
  const c = useChat();
  return (
    <Section id="ask" title="Chat."
      lead="Talk to the network. A Claude agent answers by playing the same tools this console runs on — and opens what it is talking about, right here on your screen. Conversations keep going and are kept; it is read-only, so it sees everything and signs nothing.">
      <div className="chat-wrap">
        <div className="chat-side">
          <button className="pill primary newbtn" onClick={c.newChat} disabled={c.running}>+ New chat</button>
          <div className="chat-hist">
            {c.chats.length ? c.chats.map(ch => (
              <div key={ch.id} className={'chat-item' + (ch.id === c.id ? ' sel' : '')} onClick={() => c.openChat(ch.id)}>
                <span className="t" title={ch.title}>{ch.title}</span>
                <span className="x" title="Delete" onClick={e => { e.stopPropagation(); c.deleteChat(ch.id); }}>✕</span>
              </div>
            )) : <span className="muted">No conversations yet.</span>}
          </div>
        </div>
        <div className="card"><ChatThread /></div>
      </div>
    </Section>
  );
}
