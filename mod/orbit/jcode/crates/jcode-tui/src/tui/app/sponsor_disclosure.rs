//! Tool-partner disclosure placement.
//!
//! The first `discover_tools` result in a session carries a subtle inline
//! policy notice. Keeping the notice attached to the result preserves the
//! disclosure without adding a prominent standalone system message.

use super::App;
use crate::message::ToolCall;

/// Tool name that triggers the disclosure.
pub(super) const DISCOVERY_TOOL_NAME: &str = "discover_tools";

fn tool_uses_discovery(tool: &ToolCall) -> bool {
    let name = crate::tui::ui::tools_ui::canonical_tool_name(&tool.name);
    if name == DISCOVERY_TOOL_NAME {
        return true;
    }
    if name != "batch" {
        return false;
    }

    tool.input
        .get("tool_calls")
        .and_then(|value| value.as_array())
        .is_some_and(|calls| {
            calls.iter().any(|call| {
                call.get("tool")
                    .or_else(|| call.get("name"))
                    .and_then(|value| value.as_str())
                    .is_some_and(|name| {
                        crate::tui::ui::tools_ui::canonical_tool_name(name) == DISCOVERY_TOOL_NAME
                    })
            })
        })
}

fn should_disclose(shown_this_session: bool, tool: &ToolCall) -> bool {
    !shown_this_session && tool_uses_discovery(tool)
}

impl App {
    /// Mark the first direct or batched discovery result in this session so its
    /// renderer can include the policy notice in the result details.
    pub(in crate::tui::app) fn inline_sponsor_disclosure_title(
        &mut self,
        tool: &ToolCall,
    ) -> Option<String> {
        if !should_disclose(self.sponsor_disclosure_shown_this_session, tool) {
            return None;
        }
        self.sponsor_disclosure_shown_this_session = true;
        Some(crate::sponsors::DISCOVERY_DISCLOSURE_TAG.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn tool(name: &str, input: serde_json::Value) -> ToolCall {
        ToolCall {
            id: "call_1".to_string(),
            name: name.to_string(),
            input,
            intent: None,
            thought_signature: None,
        }
    }

    #[test]
    fn discloses_once_for_direct_discovery() {
        let discovery = tool(DISCOVERY_TOOL_NAME, json!({"category": "payments"}));
        assert!(should_disclose(false, &discovery));
        assert!(!should_disclose(true, &discovery));
    }

    #[test]
    fn detects_discovery_nested_in_batch() {
        let batch = tool(
            "batch",
            json!({
                "tool_calls": [
                    {"tool": "read", "file_path": "README.md"},
                    {"tool": "discover_tools", "parameters": {"category": "payments"}}
                ]
            }),
        );
        assert!(should_disclose(false, &batch));
    }

    #[test]
    fn ignores_tools_without_discovery() {
        let read = tool("read", json!({"file_path": "README.md"}));
        assert!(!should_disclose(false, &read));
    }
}
