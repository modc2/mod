use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::path::PathBuf;
use std::sync::Arc;

mod contracts;
mod types;

use contracts::Contracts;

/// StakeTime Rust engine — exposes blockchain operations to Python via PyO3.
#[pyclass]
pub struct StakeTimeEngine {
    runtime: tokio::runtime::Runtime,
    contracts: Option<Arc<Contracts>>,
    module_dir: PathBuf,
    rpc_url: String,
    private_key: Option<String>,
    network: String,
}

#[pymethods]
impl StakeTimeEngine {
    #[new]
    #[pyo3(signature = (module_dir, rpc_url=None, private_key=None, network=None))]
    fn new(
        module_dir: &str,
        rpc_url: Option<&str>,
        private_key: Option<&str>,
        network: Option<&str>,
    ) -> PyResult<Self> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create runtime: {e}")))?;

        let rpc = rpc_url
            .unwrap_or("https://sepolia.base.org")
            .to_string();

        Ok(Self {
            runtime,
            contracts: None,
            module_dir: PathBuf::from(module_dir),
            rpc_url: rpc,
            private_key: private_key.map(|s| s.to_string()),
            network: network.unwrap_or("base_sepolia").to_string(),
        })
    }

    /// Initialize contracts from config.json + ABI artifacts.
    fn init(&mut self) -> PyResult<()> {
        let contracts = self.runtime.block_on(async {
            Contracts::load(
                &self.module_dir,
                &self.rpc_url,
                self.private_key.as_deref(),
                &self.network,
            )
            .await
        })?;
        self.contracts = Some(Arc::new(contracts));
        Ok(())
    }

    // ── Consensus reads ──────────────────────────────────────────────

    /// Get consensus block state.
    fn get_consensus(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.consensus_get_block().await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get all validators with scores.
    fn get_validators(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.get_validators().await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get single validator info.
    fn get_validator(&self, key: &str) -> PyResult<String> {
        let c = self.require_contracts()?;
        let key = key.to_string();
        self.runtime.block_on(async {
            let result = c.get_validator(&key).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    // ── Staking reads ────────────────────────────────────────────────

    /// Get user's stake positions.
    fn get_user_stakes(&self, address: &str) -> PyResult<String> {
        let c = self.require_contracts()?;
        let addr = address.to_string();
        self.runtime.block_on(async {
            let result = c.get_user_stakes(&addr).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get a single stake position.
    fn get_stake_position(&self, stake_id: u64) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.get_stake_position(stake_id).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get staker rewards.
    fn get_staker_rewards(&self, address: &str) -> PyResult<String> {
        let c = self.require_contracts()?;
        let addr = address.to_string();
        self.runtime.block_on(async {
            let result = c.get_staker_rewards(&addr).await?;
            Ok(result)
        })
    }

    // ── Registry reads ───────────────────────────────────────────────

    /// Get all subnets.
    fn get_subnets(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.get_subnets().await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get single subnet info.
    fn get_subnet(&self, subnet_id: u64) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.get_subnet(subnet_id).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get weakest subnet.
    fn get_weakest_subnet(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.get_weakest_subnet().await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get pool info for a subnet.
    fn get_pool_info(&self, subnet_id: u64) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.get_pool_info(subnet_id).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get registration cost.
    fn get_registration_cost(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.get_registration_cost().await?;
            Ok(result)
        })
    }

    // ── Write operations (transactions) ──────────────────────────────

    /// Register a validator.
    fn register(&self, key: &str, key_type: u64, commission_bps: u64) -> PyResult<String> {
        let c = self.require_contracts()?;
        let key = key.to_string();
        self.runtime.block_on(async {
            let result = c.register_validator(&key, key_type, commission_bps).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Checkin a validator.
    fn checkin(&self, key: &str) -> PyResult<String> {
        let c = self.require_contracts()?;
        let key = key.to_string();
        self.runtime.block_on(async {
            let result = c.checkin(&[key]).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Batch checkin multiple validators.
    fn batch_checkin(&self, keys: Vec<String>) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.checkin(&keys).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Produce a consensus block.
    fn produce_block(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.produce_block().await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Distribute emissions.
    fn distribute(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.distribute().await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Stake tokens on a validator.
    fn stake_on(&self, validator_key: &str, amount_wei: &str, lock_blocks: u64) -> PyResult<String> {
        let c = self.require_contracts()?;
        let key = validator_key.to_string();
        let amount = amount_wei.to_string();
        self.runtime.block_on(async {
            let result = c.stake_on(&key, &amount, lock_blocks).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Unstake a position.
    fn unstake_from(&self, stake_id: u64) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.unstake_from(stake_id).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Claim staker rewards.
    fn claim_staker_rewards(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.claim_staker_rewards().await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Claim validator rewards.
    fn claim_validator_rewards(&self, key: &str, to: Option<&str>) -> PyResult<String> {
        let c = self.require_contracts()?;
        let key = key.to_string();
        let to = to.map(|s| s.to_string());
        self.runtime.block_on(async {
            let result = c.claim_validator_rewards(&key, to.as_deref()).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Register a subnet.
    fn register_subnet(
        &self,
        name: &str,
        subnet: &str,
        staking: &str,
        consensus: &str,
    ) -> PyResult<String> {
        let c = self.require_contracts()?;
        let name = name.to_string();
        let subnet = subnet.to_string();
        let staking = staking.to_string();
        let consensus = consensus.to_string();
        self.runtime.block_on(async {
            let result = c
                .register_subnet(&name, &subnet, &staking, &consensus)
                .await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Deregister a subnet.
    fn deregister_subnet(&self, subnet_id: u64) -> PyResult<String> {
        let c = self.require_contracts()?;
        self.runtime.block_on(async {
            let result = c.deregister_subnet(subnet_id).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Boost a subnet (bonding curve deposit).
    fn boost_subnet(&self, subnet_id: u64, stt_token: &str, amount_wei: &str) -> PyResult<String> {
        let c = self.require_contracts()?;
        let stt = stt_token.to_string();
        let amount = amount_wei.to_string();
        self.runtime.block_on(async {
            let result = c.boost_subnet(subnet_id, &stt, &amount).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Sell boost shares.
    fn sell_boost(&self, subnet_id: u64, shares_wei: &str, stt_token: &str) -> PyResult<String> {
        let c = self.require_contracts()?;
        let shares = shares_wei.to_string();
        let stt = stt_token.to_string();
        self.runtime.block_on(async {
            let result = c.sell_boost(subnet_id, &shares, &stt).await?;
            Ok(serde_json::to_string(&result)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
        })
    }

    /// Get deployment info.
    fn get_deployment(&self) -> PyResult<String> {
        let c = self.require_contracts()?;
        Ok(serde_json::to_string(&c.deploy_info)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?)
    }

    /// Health check.
    fn health(&self) -> PyResult<String> {
        Ok(r#"{"status":"ok","module":"staketime"}"#.to_string())
    }
}

impl StakeTimeEngine {
    fn require_contracts(&self) -> PyResult<Arc<Contracts>> {
        self.contracts
            .clone()
            .ok_or_else(|| PyRuntimeError::new_err("Not initialized. Call init() first."))
    }
}

// ── Module registration ──────────────────────────────────────────────────

#[pymodule]
fn staketime_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<StakeTimeEngine>()?;
    Ok(())
}
