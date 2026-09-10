//! Agent-protocol client.
//!
//! The agent mod (orbit/agent) already owns prompts: a CID-pinned, shareable
//! library with an owner and an import-by-CID path. Rather than growing a second
//! prompt store here, this module BROWSES that one and hands prompts to the
//! composer — one library, many consoles, which is the whole point of the mod
//! protocol.
//!
//! Everything here degrades gracefully: if the agent module is down, the DeFi
//! console still designs, compiles and deploys. Only the AI-assist is missing.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Prompt {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub cid: Option<String>,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub builtin: bool,
}

pub struct AgentLink {
    pub base: String,
    http: reqwest::Client,
}

impl AgentLink {
    pub fn new(base: String) -> Self {
        Self {
            base,
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(120))
                .build()
                .expect("http client"),
        }
    }

    pub async fn status(&self) -> serde_json::Value {
        match self
            .http
            .get(format!("{}/health", self.base))
            .timeout(std::time::Duration::from_secs(4))
            .send()
            .await
        {
            Ok(r) => {
                let code = r.status().as_u16();
                let body = r.json::<serde_json::Value>().await.unwrap_or_default();
                serde_json::json!({ "reachable": code < 500, "url": self.base, "agent": body })
            }
            Err(e) => serde_json::json!({
                "reachable": false,
                "url": self.base,
                "error": e.to_string(),
                "hint": "start the agent module to browse the shared prompt library"
            }),
        }
    }

    /// Browse the agent protocol's prompt library.
    pub async fn prompts(&self, token: Option<&str>) -> Result<Vec<Prompt>, String> {
        let mut req = self.http.get(format!("{}/prompts", self.base));
        if let Some(t) = token {
            req = req.bearer_auth(t);
        }
        let resp = req
            .timeout(std::time::Duration::from_secs(10))
            .send()
            .await
            .map_err(|e| format!("agent module unreachable: {e}"))?;
        if !resp.status().is_success() {
            return Err(format!("agent module returned {}", resp.status()));
        }
        let body: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
        // Tolerate both {prompts:[…]} and a bare array — the agent protocol has
        // shipped both shapes.
        let list = body
            .get("prompts")
            .cloned()
            .unwrap_or_else(|| body.clone());
        serde_json::from_value(list).map_err(|e| format!("unexpected prompt payload: {e}"))
    }

    pub async fn prompt(&self, id: &str, token: Option<&str>) -> Result<Prompt, String> {
        let all = self.prompts(token).await?;
        all.into_iter()
            .find(|p| p.id == id || p.cid.as_deref() == Some(id))
            .ok_or_else(|| format!("no prompt '{id}' in the agent library"))
    }

    /// Pull a prompt someone shared as a CID into the shared library.
    pub async fn import(&self, cid: &str, token: Option<&str>) -> Result<serde_json::Value, String> {
        let mut req = self
            .http
            .post(format!("{}/prompts/import", self.base))
            .json(&serde_json::json!({ "cid": cid }));
        if let Some(t) = token {
            req = req.bearer_auth(t);
        }
        let resp = req
            .send()
            .await
            .map_err(|e| format!("agent module unreachable: {e}"))?;
        let status = resp.status();
        let body: serde_json::Value = resp.json().await.unwrap_or_default();
        if !status.is_success() {
            return Err(format!("import failed ({status}): {body}"));
        }
        Ok(body)
    }

    /// Ask the agent module to think, via the mod protocol's `forward` fn.
    pub async fn ask(
        &self,
        prompt: &str,
        token: Option<&str>,
    ) -> Result<String, String> {
        let mut req = self
            .http
            .post(format!("{}/forward", self.base))
            .json(&serde_json::json!({ "fn": "ask", "params": { "text": prompt } }));
        if let Some(t) = token {
            req = req.bearer_auth(t);
        }
        let resp = req
            .send()
            .await
            .map_err(|e| format!("agent module unreachable: {e}"))?;
        let status = resp.status();
        let body: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
        if !status.is_success() {
            return Err(format!("agent returned {status}: {body}"));
        }
        Ok(extract_text(&body))
    }
}

/// The agent protocol returns results in a few shapes depending on harness;
/// dig out the text rather than pinning one.
fn extract_text(body: &serde_json::Value) -> String {
    for key in ["result", "text", "output", "response", "content", "answer"] {
        match body.get(key) {
            Some(serde_json::Value::String(s)) => return s.clone(),
            Some(nested @ serde_json::Value::Object(_)) => {
                let inner = extract_text(nested);
                if !inner.is_empty() {
                    return inner;
                }
            }
            _ => {}
        }
    }
    if let serde_json::Value::String(s) = body {
        return s.clone();
    }
    body.to_string()
}

/// Pull the first JSON object out of an LLM reply — they fence, they preamble,
/// they apologise. We only care about the braces.
pub fn extract_json(text: &str) -> Option<serde_json::Value> {
    if let Ok(direct) = serde_json::from_str::<serde_json::Value>(text.trim()) {
        return Some(direct);
    }
    let bytes = text.as_bytes();
    let start = text.find('{')?;
    let mut depth = 0usize;
    let mut in_string = false;
    let mut escaped = false;
    for i in start..bytes.len() {
        let c = bytes[i] as char;
        if in_string {
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == '"' {
                in_string = false;
            }
            continue;
        }
        match c {
            '"' => in_string = true,
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    return serde_json::from_str(&text[start..=i]).ok();
                }
            }
            _ => {}
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digs_json_out_of_a_fenced_reply() {
        let reply = "Sure!\n```json\n{\"nodes\":[{\"id\":\"n1\"}],\"edges\":[]}\n```\nHope that helps.";
        let v = extract_json(reply).expect("should find the object");
        assert_eq!(v["nodes"][0]["id"], "n1");
    }

    #[test]
    fn ignores_braces_inside_strings() {
        let reply = "text {\"a\":\"}{\",\"b\":1} tail";
        let v = extract_json(reply).unwrap();
        assert_eq!(v["b"], 1);
    }
}
