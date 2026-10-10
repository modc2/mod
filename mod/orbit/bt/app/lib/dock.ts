/* dock signal — anything can ask the ChatDock to open without importing it.
 * A plain window event keeps lib/chat (state) and components/ChatDock (chrome)
 * decoupled: chat.newChat() raises it, the dock listens. */
export type DockMode = 'float' | 'side';
export interface DockRequest { open?: boolean; mode?: DockMode }

export const DOCK_EVENT = 'bt:dock';

export function openDock(req: DockRequest = { open: true, mode: 'side' }) {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent<DockRequest>(DOCK_EVENT, { detail: req }));
}
