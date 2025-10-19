//! Blockchain client for interacting with Subspace/Substrate nodes

use anyhow::Result;
use std::sync::Arc;
use subxt::{
    config::substrate::SubstrateConfig,
    OnlineClient,
};

/// Client for interacting with a Subspace/Substrate node
pub struct SubspaceClient {
    client: Arc<OnlineClient<SubstrateConfig>>,
}

impl SubspaceClient {
    /// Create a new client connected to the specified URL
    pub async fn new(url: &str) -> Result<Self> {
        // Use the default Substrate configuration
        let client = OnlineClient::<SubstrateConfig>::from_url(url).await?;
        
        Ok(Self {
            client: Arc::new(client),
        })
    }

    /// Get a reference to the underlying client
    pub fn client(&self) -> &OnlineClient<SubstrateConfig> {
        &self.client
    }
    
    /// Get the chain name
    pub async fn chain_name(&self) -> Result<String> {
        // For now, return a default chain name
        // In a real implementation, we would use the RPC to get the actual chain name
        Ok("Subspace".to_string())
    }
    
    /// Get the latest finalized block number
    pub async fn latest_block_number(&self) -> Result<u64> {
        // For now, return a dummy block number
        // In a real implementation, we would fetch this from the node
        Ok(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serial_test::serial;

    // This is a test that requires a running node
    #[tokio::test]
    #[serial]
    #[ignore = "requires a running node"]
    async fn test_client_connection() -> Result<()> {
        let _client = SubspaceClient::new("ws://localhost:9944").await?;
        Ok(())
    }
}
