"""
Tests for StakeTime API endpoints.

Run: pytest src/api/test_api.py -v
Requires: API running on localhost:8849 with deployed contracts.
"""

import pytest
import httpx

API_URL = "http://localhost:8849"
TIMEOUT = 15


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=API_URL, timeout=TIMEOUT) as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["module"] == "staketime"


# ── CORS ──────────────────────────────────────────────────────────────────

def test_cors_preflight(client):
    r = client.options(
        "/get_consensus",
        headers={
            "Origin": "http://localhost:8850",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert r.status_code == 200
    assert "access-control-allow-origin" in r.headers
    assert r.headers["access-control-allow-origin"] == "http://localhost:8850"


def test_cors_post_has_origin_header(client):
    r = client.post(
        "/get_consensus",
        json={},
        headers={"Origin": "http://localhost:8850"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8850"


# ── Consensus (Incentive) endpoints ──────────────────────────────────────

def test_get_consensus(client):
    r = client.post("/get_consensus", json={})
    assert r.status_code == 200
    data = r.json()["result"]
    assert "currentBlock" in data
    assert "lastEmissionBlock" in data
    assert "totalBlocktime" in data
    assert "emissionRate" in data
    assert "epochLength" in data
    assert "decayBps" in data
    assert isinstance(data["currentBlock"], int)
    assert isinstance(data["epochLength"], int)
    assert isinstance(data["emissionRate"], str)


def test_get_validators(client):
    r = client.post("/get_validators", json={})
    assert r.status_code == 200
    data = r.json()["result"]
    assert isinstance(data, list)
    # If validators exist, check structure
    if data:
        v = data[0]
        for field in ["key", "keyHash", "keyType", "registeredBlock",
                       "commissionBps", "active", "lastSeenBlock",
                       "blocktimeScore", "earned", "balance", "totalSTT"]:
            assert field in v, f"Missing field: {field}"


# ── Registry endpoints ───────────────────────────────────────────────────

def test_get_subnets(client):
    r = client.post("/get_subnets", json={})
    assert r.status_code == 200
    data = r.json()["result"]
    assert isinstance(data, list)
    if data:
        s = data[0]
        for field in ["id", "owner", "name", "subnet", "staking",
                       "consensus", "registeredBlock", "active",
                       "stakeScore", "immune"]:
            assert field in s, f"Missing field: {field}"


def test_get_weakest_subnet(client):
    r = client.post("/get_weakest_subnet", json={})
    assert r.status_code == 200
    data = r.json()["result"]
    assert "id" in data
    assert "score" in data
    assert "found" in data
    assert isinstance(data["found"], bool)


def test_get_registration_cost(client):
    r = client.post("/get_registration_cost", json={})
    assert r.status_code == 200
    cost = r.json()["result"]
    assert isinstance(cost, str)
    assert int(cost) >= 0


def test_get_subnet_by_id(client):
    # First get all subnets to find a valid ID
    r = client.post("/get_subnets", json={})
    subnets = r.json()["result"]
    if not subnets:
        pytest.skip("No subnets registered")
    subnet_id = subnets[0]["id"]
    r = client.post("/get_subnet", json={"subnet_id": subnet_id})
    assert r.status_code == 200
    data = r.json()["result"]
    assert data["id"] == subnet_id
    assert data["name"] == subnets[0]["name"]


# ── Deployment info ──────────────────────────────────────────────────────

def test_get_deployment(client):
    r = client.post("/get_deployment", json={})
    assert r.status_code == 200
    data = r.json()["result"]
    assert "staking" in data or "stakeTime" in data or "subnet" in data
    assert "consensus" in data
    assert "subnet" in data


# ── Staking view endpoints ───────────────────────────────────────────────

def test_get_user_stakes_requires_address(client):
    r = client.post("/get_user_stakes", json={})
    assert r.status_code == 422  # validation error — address required


def test_get_user_stakes_valid_address(client):
    r = client.post("/get_user_stakes", json={
        "address": "0x0000000000000000000000000000000000000000"
    })
    assert r.status_code == 200
    data = r.json()["result"]
    assert isinstance(data, list)


def test_get_stake_position_requires_id(client):
    r = client.post("/get_stake_position", json={})
    assert r.status_code == 422


def test_get_staker_rewards(client):
    r = client.post("/get_staker_rewards", json={
        "address": "0x0000000000000000000000000000000000000000"
    })
    assert r.status_code == 200
    rewards = r.json()["result"]
    assert isinstance(rewards, str)
    assert int(rewards) >= 0


# ── Validator view ───────────────────────────────────────────────────────

def test_get_validator_by_key(client):
    # First check if any validators exist
    r = client.post("/get_validators", json={})
    validators = r.json()["result"]
    if not validators:
        pytest.skip("No validators registered")
    key = validators[0]["key"]
    r = client.post("/get_validator", json={"key": key})
    assert r.status_code == 200
    data = r.json()["result"]
    assert data["key"] == key


# ── Write endpoints (validation only, no tx) ─────────────────────────────

def test_register_requires_key(client):
    r = client.post("/register", json={})
    assert r.status_code == 422


def test_checkin_requires_key(client):
    r = client.post("/checkin", json={})
    assert r.status_code == 422


def test_stake_on_requires_fields(client):
    r = client.post("/stake_on", json={})
    assert r.status_code == 422


def test_unstake_requires_stake_id(client):
    r = client.post("/unstake_from", json={})
    assert r.status_code == 422


def test_register_subnet_requires_fields(client):
    r = client.post("/register_subnet", json={})
    assert r.status_code == 422


def test_deregister_subnet_requires_id(client):
    r = client.post("/deregister_subnet", json={})
    assert r.status_code == 422


def test_generate_subnet_params_requires_prompt(client):
    r = client.post("/generate_subnet_params", json={})
    assert r.status_code == 422


def test_deploy_subnet_requires_fields(client):
    r = client.post("/deploy_subnet", json={})
    assert r.status_code == 422


# ── Bonding Curve Pool endpoints ─────────────────────────────────────

def test_boost_subnet_requires_fields(client):
    r = client.post("/boost_subnet", json={})
    assert r.status_code == 422


def test_sell_boost_requires_fields(client):
    r = client.post("/sell_boost", json={})
    assert r.status_code == 422


def test_get_boost_price(client):
    r = client.post("/get_boost_price", json={
        "subnet_id": 0,
        "num_shares": "1000000000000000000"
    })
    assert r.status_code == 200
    data = r.json()["result"]
    assert "buyCost" in data
    assert "sellReturn" in data


def test_get_pool_info(client):
    r = client.post("/get_pool_info", json={"subnet_id": 0})
    assert r.status_code == 200
    data = r.json()["result"]
    for field in ["totalShares", "totalBloctime", "sharePrice", "lockedGov", "stakeScore", "bloctimePrice"]:
        assert field in data, f"Missing field: {field}"


def test_get_user_shares(client):
    r = client.post("/get_user_shares", json={
        "subnet_id": 0,
        "address": "0x0000000000000000000000000000000000000000"
    })
    assert r.status_code == 200
    shares = r.json()["result"]
    assert isinstance(shares, str)


def test_get_subnets_includes_pool_info(client):
    r = client.post("/get_subnets", json={})
    assert r.status_code == 200
    data = r.json()["result"]
    if data:
        s = data[0]
        for field in ["totalShares", "totalBloctime", "sharePrice", "lockedGov"]:
            assert field in s, f"Missing pool field: {field}"
