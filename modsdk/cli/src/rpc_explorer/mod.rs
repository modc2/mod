// src/rpc_explorer/mod.rs

use serde::{Deserialize, Serialize};

/// Represents a single parameter for an RPC method.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RpcParameter {
    pub name: String,
    pub param_type: String, // e.g., "H256", "Option<BlockHash>", "u32", "Bytes"
    pub is_optional: bool, // True if param_type starts with "Option<"
    pub description: Option<String>,
}

/// Represents a callable RPC method.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RpcMethod {
    pub name: String, // e.g., "getBlock" (the conceptual/short name)
    pub json_rpc_method_name: String, // e.g., "chain_getBlock" (the actual RPC string)
    pub module_name: String, // e.g., "chain"
    pub params: Vec<RpcParameter>,
    pub return_type: String, // e.g., "Block", "Option<H256>"
    pub description: Option<String>,
    // We might add more fields later, like if it's a subscription or unsafe, etc.
}

/// Represents a module or group of related RPC methods.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RpcModule {
    pub name: String, // e.g., "chain", "state", "author"
    pub methods: Vec<RpcMethod>,
    pub description: Option<String>,
}

/// Top-level structure to hold all discovered RPC metadata from a node.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RpcMetadata {
    pub modules: Vec<RpcModule>,
    // Potentially a direct map for quick lookups by json_rpc_method_name
    // pub method_map: std::collections::HashMap<String, RpcMethod>,
}

// Later, we can add functions here to parse metadata or helper methods for these types.
