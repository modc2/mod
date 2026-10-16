"""
bloctime.yuma — Yuma consensus where validator stake is BlocTime.

A validator's voice is BlocTime.getVotingPower(v): the BLOC it holds plus
what is delegated to it. BLOC is dollars x seconds locked, so a 30-day lock
of $10 outvotes a 1-minute lock of $1000. Validators weight miners; the
stake-weighted kappa-quantile clips anyone voting off-consensus; miners earn
by clipped rank, validators by EMA bonds on the miners they backed early.

Three ways to run it, same numbers everywhere:
  paper   — a JSON ledger (yuma.py Ledger); stakes are paper BLOC or, with
            source(mode='live'), read from the parent's BlocTime on chain
  chain   — contracts/yuma/YumaBlocTime.sol (bloctime's hardhat project);
            mint mode pays a staketime Mod token minted by the Yuma contract
  sim     — simulate(): stateless, give it stakes + weights

Usage:
  m bloctime.yuma/state
  m bloctime.yuma/set_stake address=alice bloc=2592000      # paper BLOC
  m bloctime.yuma/source mode=live                           # use real BLOC
  m bloctime.yuma/register_validator address=0x…
  m bloctime.yuma/register_miner address=m1
  m bloctime.yuma/set_weights address=0x… weights=1,3        # one score per miner uid
  m bloctime.yuma/epoch                                     # force=true skips tempo
  m bloctime.yuma/claim address=m1
  m bloctime.yuma/simulate stakes=3000,500 weights='1,3;0,1'  # rows split by ';'
  m bloctime.yuma/deploy network=base_sepolia               # needs PRIVATE_KEY
  m bloctime.yuma/chain                                     # read the deployed contract
  m bloctime.yuma/test                                      # pytest + hardhat, same fixtures
"""
import importlib.util
from pathlib import Path

DIR = Path(__file__).parent
_spec = importlib.util.spec_from_file_location('bloctime_yuma_service', DIR / 'service.py')
svc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(svc)


class Mod:
    description = ("Yuma consensus weighted by BlocTime: validator stake = BLOC voting power "
                   "(dollars x seconds locked), emissions split to miners by clipped rank and "
                   "validators by bonds. Paper ledger + Solidity, same integer maths.")

    def forward(self, **kw):
        return self.info()

    def info(self):
        cfg = svc._cfg()
        return {'name': cfg.get('name'), 'description': cfg.get('description'),
                'rules': cfg.get('rules'), 'deployments': list(cfg.get('deployments', {})),
                'state_file': str(svc.ledger().path)}

    def state(self):
        """The paper subnet: params, validators (BLOC, weights, bonds,
        dividend), miners (incentive, consensus), pending, history."""
        return svc.state()

    def preview(self):
        """What epoch() would pay right now, without writing."""
        return svc.preview()

    def simulate(self, stakes, weights, kappa=0.5, alpha=1, emission=100,
                 owner_cut_bps=1000, miner_share_bps=5000, epochs=1):
        """Stateless Yuma. stakes: BLOC per validator ('3000,500'); weights:
        one row per validator over miners ('1,3;0,1' or JSON). alpha=1 means
        bonds have no memory."""
        return svc.simulate(stakes, weights, kappa, alpha, emission, owner_cut_bps, miner_share_bps, epochs)

    def source(self, mode=None):
        """'paper' (set_stake numbers) or 'live' (parent BlocTime getVotingPower)."""
        if mode is None:
            return {'source': svc.ledger().s.get('source', 'paper')}
        return svc.set_source(mode)

    def set_stake(self, address, bloc):
        """Paper BLOC for an address (whole tokens, or `123w` for wei)."""
        return svc.set_stake(address, bloc)

    def register_validator(self, address):
        return svc.register_validator(address)

    def register_miner(self, address):
        return svc.register_miner(address)

    def set_weights(self, address, weights):
        """weights: {uid: w}, [w0, w1, …], JSON, or '1,3,0' on the CLI."""
        return svc.set_weights(address, weights)

    def epoch(self, force=False):
        return svc.epoch(force=str(force).lower() in ('1', 'true', 'yes'))

    def claim(self, address):
        return svc.claim(address)

    def params(self, **kw):
        """Read, or set any of: tempo, emission_per_epoch, kappa, bond_alpha,
        owner_cut_bps, miner_share_bps, min_validator_bloc, max_validators, max_miners."""
        return svc.set_params(**kw) if kw else svc.ledger().p

    def reset(self):
        return svc.reset()

    def deploy(self, network='hardhat', bloctime=None, reward=None, params=None):
        """Deploy YumaBlocTime (mint mode unless reward=<erc20>). Off the
        hardhat network it targets the parent's BlocTime and needs PRIVATE_KEY."""
        return svc.deploy(network, bloctime, reward, params)

    def deployments(self):
        return svc.deployments()

    def chain(self, network=None):
        return svc.chain_state(network)

    def test(self, chains=True):
        return svc.test(chains=str(chains).lower() not in ('0', 'false', 'no'))
