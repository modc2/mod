use alloy::{
    contract::{ContractInstance, Interface},
    dyn_abi::DynSolValue,
    json_abi::JsonAbi,
    network::{Ethereum, EthereumWallet},
    primitives::{Address, FixedBytes, U256, keccak256},
    providers::{ProviderBuilder, RootProvider},
    signers::local::PrivateKeySigner,
    transports::http::{Client, Http},
};
use pyo3::exceptions::PyRuntimeError;
use pyo3::PyResult;
use serde_json::Value;
use std::path::Path;
use std::str::FromStr;
use std::sync::Arc;

use crate::types::*;

type ReadProvider = RootProvider<Http<Client>>;

type SignerProvider = alloy::providers::fillers::FillProvider<
    alloy::providers::fillers::JoinFill<
        alloy::providers::fillers::JoinFill<
            alloy::providers::Identity,
            alloy::providers::fillers::JoinFill<
                alloy::providers::fillers::GasFiller,
                alloy::providers::fillers::JoinFill<
                    alloy::providers::fillers::BlobGasFiller,
                    alloy::providers::fillers::JoinFill<
                        alloy::providers::fillers::NonceFiller,
                        alloy::providers::fillers::ChainIdFiller,
                    >,
                >,
            >,
        >,
        alloy::providers::fillers::WalletFiller<EthereumWallet>,
    >,
    RootProvider<Http<Client>>,
    Http<Client>,
    Ethereum,
>;

pub struct Contracts {
    provider: Arc<ReadProvider>,
    signer_provider: Option<Arc<SignerProvider>>,
    signer_address: Option<Address>,
    staking_abi: JsonAbi,
    consensus_abi: JsonAbi,
    mod_abi: JsonAbi,
    registry_abi: JsonAbi,
    staking_addr: Address,
    consensus_addr: Address,
    mod_addr: Address,
    registry_addr: Option<Address>,
    governance_addr: Option<Address>,
    pub deploy_info: DeployInfo,
}

impl Contracts {
    pub async fn load(
        module_dir: &Path,
        rpc_url: &str,
        private_key: Option<&str>,
        network: &str,
    ) -> PyResult<Self> {
        // Load config.json
        let config_path = module_dir.join("config.json");
        let config_str = std::fs::read_to_string(&config_path)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to read config.json: {e}")))?;
        let config: Value = serde_json::from_str(&config_str)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to parse config.json: {e}")))?;

        let contracts = config
            .get("contracts")
            .and_then(|c| c.get(network))
            .ok_or_else(|| {
                PyRuntimeError::new_err(format!("No contracts found for network '{network}'"))
            })?;

        let staking_addr = Self::parse_address(
            contracts
                .get("staking")
                .or_else(|| contracts.get("stakeTime"))
                .and_then(|v| v.as_str())
                .unwrap_or(""),
        )?;
        let consensus_addr =
            Self::parse_address(contracts.get("consensus").and_then(|v| v.as_str()).unwrap_or(""))?;
        let mod_addr = Self::parse_address(
            contracts
                .get("subnet")
                .or_else(|| contracts.get("mod"))
                .and_then(|v| v.as_str())
                .unwrap_or(""),
        )?;
        let registry_addr = contracts
            .get("registry")
            .and_then(|v| v.as_str())
            .and_then(|s| Self::parse_address(s).ok());
        let governance_addr = contracts
            .get("governanceToken")
            .and_then(|v| v.as_str())
            .and_then(|s| Self::parse_address(s).ok());

        // Load ABIs
        let artifacts = module_dir.join("artifacts").join("src").join("contracts");
        let staking_abi = Self::load_abi(&artifacts.join("staking/StakeTime.sol/StakeTime.json"))?;
        let consensus_abi =
            Self::load_abi(&artifacts.join("consensus/yuma/ConsensusYuma.sol/ConsensusYuma.json"))?;
        let mod_abi = Self::load_abi(&artifacts.join("mod/Mod.sol/Mod.json"))?;
        let registry_abi = Self::load_abi(&artifacts.join("registry/Registry.sol/Registry.json"))
            .unwrap_or_else(|_| JsonAbi::new());

        // Create providers
        let url = rpc_url
            .parse()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid RPC URL: {e}")))?;
        let provider = Arc::new(ProviderBuilder::new().on_http(url));

        let (signer_provider, signer_address) = if let Some(pk) = private_key {
            let pk_clean = pk.strip_prefix("0x").unwrap_or(pk);
            let signer: PrivateKeySigner = pk_clean
                .parse()
                .map_err(|e| PyRuntimeError::new_err(format!("Invalid private key: {e}")))?;
            let addr = signer.address();
            let wallet = EthereumWallet::from(signer);
            let url2 = rpc_url
                .parse()
                .map_err(|e| PyRuntimeError::new_err(format!("Invalid RPC URL: {e}")))?;
            let sp = ProviderBuilder::new()
                .with_recommended_fillers()
                .wallet(wallet)
                .on_http(url2);
            (Some(Arc::new(sp)), Some(addr))
        } else {
            (None, None)
        };

        let deploy_info = DeployInfo {
            subnet: contracts.get("subnet").and_then(|v| v.as_str()).map(String::from),
            stake_time: contracts.get("stakeTime").and_then(|v| v.as_str()).map(String::from),
            staking: contracts.get("staking").and_then(|v| v.as_str()).map(String::from),
            consensus: contracts.get("consensus").and_then(|v| v.as_str()).map(String::from),
            governance_token: contracts.get("governanceToken").and_then(|v| v.as_str()).map(String::from),
            registry: contracts.get("registry").and_then(|v| v.as_str()).map(String::from),
            chain_id: contracts.get("chainId").and_then(|v| v.as_u64()),
        };

        Ok(Self {
            provider,
            signer_provider,
            signer_address,
            staking_abi,
            consensus_abi,
            mod_abi,
            registry_abi,
            staking_addr,
            consensus_addr,
            mod_addr,
            registry_addr,
            governance_addr,
            deploy_info,
        })
    }

    fn parse_address(s: &str) -> PyResult<Address> {
        s.parse::<Address>()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid address '{s}': {e}")))
    }

    fn load_abi(path: &Path) -> PyResult<JsonAbi> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to read ABI {}: {e}", path.display())))?;
        let artifact: Value = serde_json::from_str(&content)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to parse ABI JSON: {e}")))?;
        let abi_value = artifact
            .get("abi")
            .ok_or_else(|| PyRuntimeError::new_err("No 'abi' field in artifact"))?;
        let abi: JsonAbi = serde_json::from_value(abi_value.clone())
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to parse ABI: {e}")))?;
        Ok(abi)
    }

    fn require_signer(&self) -> PyResult<(&Arc<SignerProvider>, Address)> {
        let sp = self
            .signer_provider
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("No private key configured"))?;
        let addr = self.signer_address.unwrap();
        Ok((sp, addr))
    }

    fn staking_contract(&self) -> ContractInstance<Http<Client>, &ReadProvider, Ethereum> {
        ContractInstance::new(
            self.staking_addr,
            self.provider.as_ref(),
            Interface::new(self.staking_abi.clone()),
        )
    }

    fn consensus_contract(&self) -> ContractInstance<Http<Client>, &ReadProvider, Ethereum> {
        ContractInstance::new(
            self.consensus_addr,
            self.provider.as_ref(),
            Interface::new(self.consensus_abi.clone()),
        )
    }

    fn registry_contract(&self) -> PyResult<ContractInstance<Http<Client>, &ReadProvider, Ethereum>> {
        let addr = self
            .registry_addr
            .ok_or_else(|| PyRuntimeError::new_err("Registry not deployed"))?;
        Ok(ContractInstance::new(
            addr,
            self.provider.as_ref(),
            Interface::new(self.registry_abi.clone()),
        ))
    }

    // ── Read methods ────────────────────────��────────────────────────

    pub async fn consensus_get_block(&self) -> PyResult<ConsensusState> {
        let contract = self.consensus_contract();
        let result = contract
            .function("getBlock", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let decay_result = contract
            .function("decayBps", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        // Parse tuple result
        let vals = &result;
        Ok(ConsensusState {
            current_block: Self::extract_u64(&vals[0]),
            last_emission_block: Self::extract_u64(&vals[1]),
            total_blocktime: Self::extract_u64(&vals[2]),
            emission_rate: Self::extract_u256_string(&vals[3]),
            epoch_length: Self::extract_u64(&vals[4]),
            decay_bps: Self::extract_u64(&decay_result[0]),
        })
    }

    pub async fn get_validators(&self) -> PyResult<Vec<ValidatorInfo>> {
        let staking = self.staking_contract();
        let consensus = self.consensus_contract();

        let count_result = staking
            .function("validatorCount", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let count = Self::extract_u64(&count_result[0]);
        let mut validators = Vec::new();

        for i in 0..count {
            let kh_result = staking
                .function("getValidatorKeyHash", &[DynSolValue::Uint(U256::from(i), 256)])
                .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                .call()
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

            let key_hash = Self::extract_bytes32(&kh_result[0]);

            let v_result = staking
                .function("getValidatorByHash", &[DynSolValue::FixedBytes(key_hash, 32)])
                .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                .call()
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

            let score_result = consensus
                .function("getValidatorScore", &[DynSolValue::FixedBytes(key_hash, 32)])
                .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                .call()
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

            let bal_result = consensus
                .function("validatorBalances", &[DynSolValue::FixedBytes(key_hash, 32)])
                .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                .call()
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

            let stt_result = staking
                .function(
                    "getValidatorTotalMintedByHash",
                    &[DynSolValue::FixedBytes(key_hash, 32)],
                )
                .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                .call()
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

            // Parse validator tuple
            let v_vals = &v_result;
            validators.push(ValidatorInfo {
                key: Self::extract_string(&v_vals[0]),
                key_hash: format!("0x{}", hex::encode(key_hash)),
                key_type: Self::extract_u64(&v_vals[1]),
                registered_block: Self::extract_u64(&v_vals[2]),
                commission_bps: Self::extract_u64(&v_vals[3]),
                active: Self::extract_bool(&v_vals[4]),
                last_seen_block: Self::extract_u64(&score_result[0]),
                blocktime_score: Self::extract_u64(&score_result[1]),
                earned: Self::extract_u256_string(&score_result[2]),
                balance: Self::extract_u256_string(&bal_result[0]),
                total_stt: Self::extract_u256_string(&stt_result[0]),
            });
        }

        Ok(validators)
    }

    pub async fn get_validator(&self, key: &str) -> PyResult<ValidatorInfo> {
        let staking = self.staking_contract();
        let consensus = self.consensus_contract();

        let v_result = staking
            .function("getValidator", &[DynSolValue::String(key.to_string())])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let key_hash = keccak256(key.as_bytes());

        let score_result = consensus
            .function(
                "getValidatorScore",
                &[DynSolValue::FixedBytes(key_hash.into(), 32)],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let bal_result = consensus
            .function(
                "validatorBalances",
                &[DynSolValue::FixedBytes(key_hash.into(), 32)],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let stt_result = staking
            .function(
                "getValidatorTotalMintedByHash",
                &[DynSolValue::FixedBytes(key_hash.into(), 32)],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let v_vals = &v_result;
        Ok(ValidatorInfo {
            key: Self::extract_string(&v_vals[0]),
            key_hash: format!("0x{}", hex::encode(key_hash)),
            key_type: Self::extract_u64(&v_vals[1]),
            registered_block: Self::extract_u64(&v_vals[2]),
            commission_bps: Self::extract_u64(&v_vals[3]),
            active: Self::extract_bool(&v_vals[4]),
            last_seen_block: Self::extract_u64(&score_result[0]),
            blocktime_score: Self::extract_u64(&score_result[1]),
            earned: Self::extract_u256_string(&score_result[2]),
            balance: Self::extract_u256_string(&bal_result[0]),
            total_stt: Self::extract_u256_string(&stt_result[0]),
        })
    }

    pub async fn get_user_stakes(&self, address: &str) -> PyResult<Vec<StakePosition>> {
        let staking = self.staking_contract();
        let addr = address
            .parse::<Address>()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid address: {e}")))?;

        let ids_result = staking
            .function("getUserStakeIds", &[DynSolValue::Address(addr)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let ids = Self::extract_uint_array(&ids_result[0]);
        let mut stakes = Vec::new();

        for id in ids {
            let pos = self.get_stake_position(id).await?;
            stakes.push(pos);
        }

        Ok(stakes)
    }

    pub async fn get_stake_position(&self, stake_id: u64) -> PyResult<StakePosition> {
        let staking = self.staking_contract();

        let result = staking
            .function(
                "getStakePosition",
                &[DynSolValue::Uint(U256::from(stake_id), 256)],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let vals = &result;
        Ok(StakePosition {
            stake_id,
            staker: Self::extract_address_string(&vals[0]),
            validator_key_hash: Self::extract_bytes32_hex(&vals[1]),
            amount: Self::extract_u256_string(&vals[2]),
            start_block: Self::extract_u64(&vals[3]),
            lock_blocks: Self::extract_u64(&vals[4]),
            minted_balance: Self::extract_u256_string(&vals[5]),
            blocks_remaining: Self::extract_u64(&vals[6]),
        })
    }

    pub async fn get_staker_rewards(&self, address: &str) -> PyResult<String> {
        let consensus = self.consensus_contract();
        let addr = address
            .parse::<Address>()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid address: {e}")))?;

        let result = consensus
            .function("getStakerRewards", &[DynSolValue::Address(addr)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        Ok(Self::extract_u256_string(&result[0]))
    }

    pub async fn get_subnets(&self) -> PyResult<Vec<SubnetInfo>> {
        let registry = self.registry_contract()?;

        let result = registry
            .function("getAllSubnets", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let mut subnets = Vec::new();
        if let Some(arr) = result[0].as_array() {
            for item in arr {
                if let Some(tuple) = item.as_tuple() {
                    let id = Self::extract_u64(&tuple[0]);
                    let score_result = registry
                        .function("getStakeScore", &[DynSolValue::Uint(U256::from(id), 256)])
                        .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                        .call()
                        .await
                        .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;
                    let immune_result = registry
                        .function("isImmune", &[DynSolValue::Uint(U256::from(id), 256)])
                        .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                        .call()
                        .await
                        .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;
                    let pool_result = registry
                        .function("getPoolInfo", &[DynSolValue::Uint(U256::from(id), 256)])
                        .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                        .call()
                        .await
                        .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

                    subnets.push(SubnetInfo {
                        id,
                        owner: Self::extract_address_string(&tuple[1]),
                        name: Self::extract_string(&tuple[2]),
                        subnet: Self::extract_address_string(&tuple[3]),
                        staking: Self::extract_address_string(&tuple[4]),
                        consensus: Self::extract_address_string(&tuple[5]),
                        registered_block: Self::extract_u64(&tuple[6]),
                        active: Self::extract_bool(&tuple[7]),
                        stake_score: Self::extract_u256_string(&score_result[0]),
                        immune: Self::extract_bool(&immune_result[0]),
                        total_shares: Some(Self::extract_u256_string(&pool_result[0])),
                        total_bloctime: Some(Self::extract_u256_string(&pool_result[1])),
                        share_price: Some(Self::extract_u256_string(&pool_result[2])),
                        locked_gov: Some(Self::extract_u256_string(&pool_result[3])),
                    });
                }
            }
        }

        Ok(subnets)
    }

    pub async fn get_subnet(&self, subnet_id: u64) -> PyResult<SubnetInfo> {
        let registry = self.registry_contract()?;

        let result = registry
            .function("getSubnet", &[DynSolValue::Uint(U256::from(subnet_id), 256)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let score_result = registry
            .function("getStakeScore", &[DynSolValue::Uint(U256::from(subnet_id), 256)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let immune_result = registry
            .function("isImmune", &[DynSolValue::Uint(U256::from(subnet_id), 256)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let vals = &result;
        Ok(SubnetInfo {
            id: Self::extract_u64(&vals[0]),
            owner: Self::extract_address_string(&vals[1]),
            name: Self::extract_string(&vals[2]),
            subnet: Self::extract_address_string(&vals[3]),
            staking: Self::extract_address_string(&vals[4]),
            consensus: Self::extract_address_string(&vals[5]),
            registered_block: Self::extract_u64(&vals[6]),
            active: Self::extract_bool(&vals[7]),
            stake_score: Self::extract_u256_string(&score_result[0]),
            immune: Self::extract_bool(&immune_result[0]),
            total_shares: None,
            total_bloctime: None,
            share_price: None,
            locked_gov: None,
        })
    }

    pub async fn get_weakest_subnet(&self) -> PyResult<WeakestSubnet> {
        let registry = self.registry_contract()?;
        let result = registry
            .function("getWeakestSubnet", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        Ok(WeakestSubnet {
            id: Self::extract_u64(&result[0]),
            score: Self::extract_u256_string(&result[1]),
            found: Self::extract_bool(&result[2]),
        })
    }

    pub async fn get_pool_info(&self, subnet_id: u64) -> PyResult<PoolInfo> {
        let registry = self.registry_contract()?;

        let pool_result = registry
            .function("getPoolInfo", &[DynSolValue::Uint(U256::from(subnet_id), 256)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let score_result = registry
            .function("getStakeScore", &[DynSolValue::Uint(U256::from(subnet_id), 256)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;

        let bloctime_price = match registry
            .function("getBloctimePrice", &[DynSolValue::Uint(U256::from(subnet_id), 256)])
        {
            Ok(call) => match call.call().await {
                Ok(r) => Self::extract_u256_string(&r[0]),
                Err(_) => "0".to_string(),
            },
            Err(_) => "0".to_string(),
        };

        Ok(PoolInfo {
            total_shares: Self::extract_u256_string(&pool_result[0]),
            total_bloctime: Self::extract_u256_string(&pool_result[1]),
            share_price: Self::extract_u256_string(&pool_result[2]),
            locked_gov: Self::extract_u256_string(&pool_result[3]),
            stake_score: Self::extract_u256_string(&score_result[0]),
            bloctime_price,
        })
    }

    pub async fn get_registration_cost(&self) -> PyResult<String> {
        let registry = self.registry_contract()?;
        let result = registry
            .function("getRegistrationCost", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .call()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;
        Ok(Self::extract_u256_string(&result[0]))
    }

    // ── Write methods (transactions) ────────────���────────────────────

    pub async fn register_validator(
        &self,
        key: &str,
        key_type: u64,
        commission_bps: u64,
    ) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let contract = ContractInstance::new(
            self.staking_addr,
            sp.as_ref(),
            Interface::new(self.staking_abi.clone()),
        );

        let receipt = contract
            .function(
                "registerValidatorAdmin",
                &[
                    DynSolValue::String(key.to_string()),
                    DynSolValue::Uint(U256::from(key_type), 256),
                    DynSolValue::Uint(U256::from(commission_bps), 256),
                ],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn checkin(&self, keys: &[String]) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let contract = ContractInstance::new(
            self.consensus_addr,
            sp.as_ref(),
            Interface::new(self.consensus_abi.clone()),
        );

        let key_values: Vec<DynSolValue> = keys
            .iter()
            .map(|k| DynSolValue::String(k.clone()))
            .collect();

        let receipt = contract
            .function("batchCheckin", &[DynSolValue::Array(key_values)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .gas(300000)
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn produce_block(&self) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let contract = ContractInstance::new(
            self.consensus_addr,
            sp.as_ref(),
            Interface::new(self.consensus_abi.clone()),
        );

        let receipt = contract
            .function("produceBlock", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn distribute(&self) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let contract = ContractInstance::new(
            self.consensus_addr,
            sp.as_ref(),
            Interface::new(self.consensus_abi.clone()),
        );

        let receipt = contract
            .function("distributeEmissions", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .gas(1_000_000)
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn stake_on(
        &self,
        validator_key: &str,
        amount_wei: &str,
        lock_blocks: u64,
    ) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let amount = U256::from_str(amount_wei)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid amount: {e}")))?;

        // Approve token first
        let mod_contract = ContractInstance::new(
            self.mod_addr,
            sp.as_ref(),
            Interface::new(self.mod_abi.clone()),
        );
        mod_contract
            .function(
                "approve",
                &[
                    DynSolValue::Address(self.staking_addr),
                    DynSolValue::Uint(amount, 256),
                ],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Approve failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Approve receipt failed: {e}")))?;

        // Stake
        let staking_contract = ContractInstance::new(
            self.staking_addr,
            sp.as_ref(),
            Interface::new(self.staking_abi.clone()),
        );
        let receipt = staking_contract
            .function(
                "stakeOn",
                &[
                    DynSolValue::String(validator_key.to_string()),
                    DynSolValue::Uint(amount, 256),
                    DynSolValue::Uint(U256::from(lock_blocks), 256),
                ],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn unstake_from(&self, stake_id: u64) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let contract = ContractInstance::new(
            self.staking_addr,
            sp.as_ref(),
            Interface::new(self.staking_abi.clone()),
        );

        let receipt = contract
            .function("unstakeFrom", &[DynSolValue::Uint(U256::from(stake_id), 256)])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn claim_staker_rewards(&self) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let contract = ContractInstance::new(
            self.consensus_addr,
            sp.as_ref(),
            Interface::new(self.consensus_abi.clone()),
        );

        let receipt = contract
            .function("claimStakerRewards", &[])
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn claim_validator_rewards(&self, key: &str, to: Option<&str>) -> PyResult<TxResult> {
        let (sp, addr) = self.require_signer()?;
        let to_addr = match to {
            Some(a) => a
                .parse::<Address>()
                .map_err(|e| PyRuntimeError::new_err(format!("Invalid to address: {e}")))?,
            None => addr,
        };

        let contract = ContractInstance::new(
            self.consensus_addr,
            sp.as_ref(),
            Interface::new(self.consensus_abi.clone()),
        );

        let receipt = contract
            .function(
                "claimValidatorRewards",
                &[
                    DynSolValue::String(key.to_string()),
                    DynSolValue::Address(to_addr),
                ],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn register_subnet(
        &self,
        name: &str,
        subnet: &str,
        staking: &str,
        consensus: &str,
    ) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let registry_addr = self
            .registry_addr
            .ok_or_else(|| PyRuntimeError::new_err("Registry not deployed"))?;

        // Approve governance token for registration cost
        if let Some(gov_addr) = self.governance_addr {
            let registry_read = self.registry_contract()?;
            let cost_result = registry_read
                .function("getRegistrationCost", &[])
                .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                .call()
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("Call failed: {e}")))?;
            let cost = Self::extract_u256(&cost_result[0]);

            if cost > U256::ZERO {
                let gov_contract = ContractInstance::new(
                    gov_addr,
                    sp.as_ref(),
                    Interface::new(self.mod_abi.clone()),
                );
                gov_contract
                    .function(
                        "approve",
                        &[
                            DynSolValue::Address(registry_addr),
                            DynSolValue::Uint(cost, 256),
                        ],
                    )
                    .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
                    .send()
                    .await
                    .map_err(|e| PyRuntimeError::new_err(format!("Approve failed: {e}")))?
                    .get_receipt()
                    .await
                    .map_err(|e| PyRuntimeError::new_err(format!("Approve receipt: {e}")))?;
            }
        }

        let contract = ContractInstance::new(
            registry_addr,
            sp.as_ref(),
            Interface::new(self.registry_abi.clone()),
        );

        let subnet_addr = subnet
            .parse::<Address>()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid subnet address: {e}")))?;
        let staking_addr = staking
            .parse::<Address>()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid staking address: {e}")))?;
        let consensus_addr = consensus
            .parse::<Address>()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid consensus address: {e}")))?;

        let receipt = contract
            .function(
                "registerSubnet",
                &[
                    DynSolValue::String(name.to_string()),
                    DynSolValue::Address(subnet_addr),
                    DynSolValue::Address(staking_addr),
                    DynSolValue::Address(consensus_addr),
                ],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn deregister_subnet(&self, subnet_id: u64) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let registry_addr = self
            .registry_addr
            .ok_or_else(|| PyRuntimeError::new_err("Registry not deployed"))?;

        let contract = ContractInstance::new(
            registry_addr,
            sp.as_ref(),
            Interface::new(self.registry_abi.clone()),
        );

        let receipt = contract
            .function(
                "deregisterSubnet",
                &[DynSolValue::Uint(U256::from(subnet_id), 256)],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn boost_subnet(
        &self,
        subnet_id: u64,
        stt_token: &str,
        amount_wei: &str,
    ) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let registry_addr = self
            .registry_addr
            .ok_or_else(|| PyRuntimeError::new_err("Registry not deployed"))?;
        let stt_addr = stt_token
            .parse::<Address>()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid STT address: {e}")))?;
        let amount = U256::from_str(amount_wei)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid amount: {e}")))?;

        // Approve STT for registry
        let stt_contract = ContractInstance::new(
            stt_addr,
            sp.as_ref(),
            Interface::new(self.staking_abi.clone()),
        );
        stt_contract
            .function(
                "approve",
                &[
                    DynSolValue::Address(registry_addr),
                    DynSolValue::Uint(amount, 256),
                ],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Approve failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Approve receipt: {e}")))?;

        let contract = ContractInstance::new(
            registry_addr,
            sp.as_ref(),
            Interface::new(self.registry_abi.clone()),
        );

        let receipt = contract
            .function(
                "boostSubnet",
                &[
                    DynSolValue::Uint(U256::from(subnet_id), 256),
                    DynSolValue::Address(stt_addr),
                    DynSolValue::Uint(amount, 256),
                ],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    pub async fn sell_boost(
        &self,
        subnet_id: u64,
        shares_wei: &str,
        stt_token: &str,
    ) -> PyResult<TxResult> {
        let (sp, _) = self.require_signer()?;
        let registry_addr = self
            .registry_addr
            .ok_or_else(|| PyRuntimeError::new_err("Registry not deployed"))?;
        let shares = U256::from_str(shares_wei)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid shares: {e}")))?;
        let stt_addr = stt_token
            .parse::<Address>()
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid STT address: {e}")))?;

        let contract = ContractInstance::new(
            registry_addr,
            sp.as_ref(),
            Interface::new(self.registry_abi.clone()),
        );

        let receipt = contract
            .function(
                "sellBoost",
                &[
                    DynSolValue::Uint(U256::from(subnet_id), 256),
                    DynSolValue::Uint(shares, 256),
                    DynSolValue::Address(stt_addr),
                ],
            )
            .map_err(|e| PyRuntimeError::new_err(format!("ABI error: {e}")))?
            .send()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Send failed: {e}")))?
            .get_receipt()
            .await
            .map_err(|e| PyRuntimeError::new_err(format!("Receipt failed: {e}")))?;

        Ok(TxResult {
            success: receipt.status(),
            tx_hash: format!("0x{}", hex::encode(receipt.transaction_hash.as_slice())),
        })
    }

    // ── Helpers ────────────────────────────────────��─────────────────

    fn extract_u64(val: &DynSolValue) -> u64 {
        match val {
            DynSolValue::Uint(u, _) => u.to::<u64>(),
            _ => 0,
        }
    }

    fn extract_u256(val: &DynSolValue) -> U256 {
        match val {
            DynSolValue::Uint(u, _) => *u,
            _ => U256::ZERO,
        }
    }

    fn extract_u256_string(val: &DynSolValue) -> String {
        match val {
            DynSolValue::Uint(u, _) => u.to_string(),
            _ => "0".to_string(),
        }
    }

    fn extract_string(val: &DynSolValue) -> String {
        match val {
            DynSolValue::String(s) => s.clone(),
            _ => String::new(),
        }
    }

    fn extract_bool(val: &DynSolValue) -> bool {
        match val {
            DynSolValue::Bool(b) => *b,
            _ => false,
        }
    }

    fn extract_address_string(val: &DynSolValue) -> String {
        match val {
            DynSolValue::Address(a) => format!("{a:?}"),
            _ => String::new(),
        }
    }

    fn extract_bytes32(val: &DynSolValue) -> FixedBytes<32> {
        match val {
            DynSolValue::FixedBytes(w, 32) => *w,
            _ => FixedBytes::ZERO,
        }
    }

    fn extract_bytes32_hex(val: &DynSolValue) -> String {
        match val {
            DynSolValue::FixedBytes(w, 32) => format!("0x{}", hex::encode(w.as_slice())),
            _ => "0x".to_string(),
        }
    }

    fn extract_uint_array(val: &DynSolValue) -> Vec<u64> {
        match val {
            DynSolValue::Array(arr) => arr.iter().map(|v| Self::extract_u64(v)).collect(),
            _ => Vec::new(),
        }
    }
}
