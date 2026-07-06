use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Chain {
    Ethereum,
    Arbitrum,
    Base,
    Polygon,
    Optimism,
}

impl Chain {
    pub fn all() -> &'static [Chain] {
        &[
            Chain::Ethereum,
            Chain::Arbitrum,
            Chain::Base,
            Chain::Polygon,
            Chain::Optimism,
        ]
    }

    pub fn from_str(s: &str) -> Option<Chain> {
        match s.to_lowercase().as_str() {
            "ethereum" | "eth" => Some(Chain::Ethereum),
            "arbitrum" | "arb" => Some(Chain::Arbitrum),
            "base" => Some(Chain::Base),
            "polygon" | "matic" => Some(Chain::Polygon),
            "optimism" | "op" => Some(Chain::Optimism),
            _ => None,
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            Chain::Ethereum => "ethereum",
            Chain::Arbitrum => "arbitrum",
            Chain::Base => "base",
            Chain::Polygon => "polygon",
            Chain::Optimism => "optimism",
        }
    }
}

impl std::fmt::Display for Chain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.name())
    }
}
