// Deploy NamespaceRegistry and wire it to an existing StakeTime (STT) token.
//
// Reads the STT token address from staketime/config.json (or env STT_TOKEN).
// Writes the deployment back into modchain/config.json under
// contracts.<network>.

const path = require('path');
const fs = require('fs');
const hre = require('hardhat');

async function main() {
  const network = hre.network.name;
  const [deployer] = await hre.ethers.getSigners();
  console.log(`Deploying NamespaceRegistry on ${network} from ${deployer.address}`);

  const stt = process.env.STT_TOKEN || resolveSttFromStaketime(network);
  if (!stt) throw new Error(
    'STT token address not found. Set STT_TOKEN env or deploy staketime first.'
  );
  console.log(`Using STT token: ${stt}`);

  const minClaimStake = process.env.MIN_CLAIM_STAKE || hre.ethers.parseEther('1');
  console.log(`minClaimStake: ${minClaimStake.toString()}`);

  const Registry = await hre.ethers.getContractFactory('NamespaceRegistry');
  const registry = await Registry.deploy(stt, minClaimStake);
  await registry.waitForDeployment();
  const addr = await registry.getAddress();
  console.log(`NamespaceRegistry deployed at: ${addr}`);

  writeConfig(network, {
    namespaceRegistry: addr,
    stt,
    minClaimStake: minClaimStake.toString(),
    chainId: (await hre.ethers.provider.getNetwork()).chainId.toString(),
    deployedAt: new Date().toISOString(),
  });
}

function resolveSttFromStaketime(network) {
  const p = path.resolve(__dirname, '..', '..', 'staketime', 'config.json');
  if (!fs.existsSync(p)) return null;
  const cfg = JSON.parse(fs.readFileSync(p, 'utf8'));
  const c = (cfg.contracts || {})[network] || {};
  return c.stakeTime || c.staking || null;
}

function writeConfig(network, data) {
  const p = path.resolve(__dirname, '..', 'config.json');
  let cfg = {};
  if (fs.existsSync(p)) cfg = JSON.parse(fs.readFileSync(p, 'utf8'));
  cfg.contracts = cfg.contracts || {};
  cfg.contracts[network] = { ...(cfg.contracts[network] || {}), ...data };
  fs.writeFileSync(p, JSON.stringify(cfg, null, 2));
  console.log(`Wrote ${p}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
