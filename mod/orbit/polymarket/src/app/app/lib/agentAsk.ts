// Hand the desk agent a question from anywhere in the console: opens the
// left agent column (AgentShell listens) and HelpAgent sends the question in
// the same event. If the agent is mid-run, the question parks in its input
// instead of being dropped.

import { OPEN_AGENT_EVENT } from "../components/HelpAgent";

export function askDeskAgent(question: string): void {
  window.dispatchEvent(new CustomEvent(OPEN_AGENT_EVENT, { detail: { ask: question } }));
}
