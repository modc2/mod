// copilot — GitHub Copilot CLI agent, written as a JavaScript contract.
// Subclass of AgentContract; same shape as the Python and Rust counterparts.

const { AgentContract, runCli } = require("../dev/src/agent_base.js");

class Mod extends AgentContract {
  static NAME = "copilot";
  static ICON = "◐";
  static COLOR = "#2ea043";
  static BINARY = "gh-copilot";
  static DEFAULT_MODEL = "copilot";
  static ENV_KEY = "GITHUB_TOKEN";
  static DESCRIPTION = "GitHub Copilot CLI agent (JavaScript contract)";

  static ABI = {
    info:    { kind: "view", ownerOnly: false, doc: "Return agent info" },
    health:  { kind: "view", ownerOnly: false, doc: "Probe Copilot CLI" },
    submit:  { kind: "tx",   ownerOnly: false, doc: "Queue a prompt for gh-copilot" },
    suggest: { kind: "tx",   ownerOnly: false, doc: "Get a shell command suggestion" },
    jobs:    { kind: "view", ownerOnly: false, doc: "List recent jobs" },
  };

  buildArgs(prompt, _model, workDir) {
    // gh-copilot suggest <prompt>
    return ["suggest", "--target", "shell", "--workdir", workDir, prompt];
  }

  // Custom tx method specific to Copilot
  suggest({ prompt } = {}) {
    const { execSync } = require("child_process");
    try {
      const out = execSync(`gh copilot suggest --target shell "${prompt.replace(/"/g, '\\"')}"`,
        { timeout: 30000 }).toString();
      this.state.jobs_submitted = (this.state.jobs_submitted || 0) + 1;
      this.emit("suggestion", { prompt: prompt.slice(0, 80) });
      this._saveState();
      return { suggestion: out };
    } catch (e) {
      this.emit("suggest_failed", { error: e.message });
      this._saveState();
      return { error: e.message };
    }
  }
}

if (require.main === module) runCli(Mod);
module.exports = Mod;
