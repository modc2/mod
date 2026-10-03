// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * ZKNet — proof-of-complexity.
 *
 * A groth16 verifier, a complexity meter, and an ERC-20 whose only mint path
 * runs through both. You hand it a proof; it checks the pairing equation on
 * the alt_bn128 precompiles, prices the circuit that produced it, and mints
 * ZKW in proportion to that price. Harder circuits mint more. Nothing else
 * mints at all — there is no owner mint, no premine, no treasury allocation.
 *
 * Three things this contract can know for itself, and one it cannot:
 *
 *   it knows   the proof verifies                (pairing check, on-chain)
 *   it knows   how many public inputs there are  (len(IC) - 1, bound to the key)
 *   it knows   which proof system and curve      (the key would not verify otherwise)
 *   it CANNOT  know the constraint count         — nothing in a groth16 key
 *              commits to it
 *
 * So the constraint count is *declared*, and the contract does the only two
 * honest things it can with a declaration. It BINDS it: the first claim
 * against a circuit digest fixes that circuit's size forever, so a prover
 * cannot tell the chain two different stories about one circuit. And it
 * STAKES it: most of the mint does not vest until a challenge window closes,
 * and an attestor who has actually read the .r1cs can reprice the circuit
 * downward inside that window — which repays the vesting claim at the
 * corrected size and burns the difference. The declarer's own upside is the
 * bond. The part of the mint that rests on nothing the caller said is liquid
 * immediately.
 *
 * The dollar figure is a quote, not a promise. askPrice() is the contract's
 * own bonding curve — what it would sell the next token for. redeemValue()
 * is the reserve-backed floor, and it is zero until somebody funds the
 * reserve. Both are reported, always, so nobody has to guess which one they
 * are being shown.
 *
 * G2 points are in PRECOMPILE order (imaginary limb first): a point snarkjs
 * writes as [[x0,x1],[y0,y1]] is passed here as [[x1,x0],[y1,y0]]. The Python
 * side does that swap in one place; see src/evm.py.
 */
contract ZKNet {
    // ── units ────────────────────────────────────────────────────────────
    uint256 internal constant WAD = 1e18;

    // ── emission ─────────────────────────────────────────────────────────
    /// ZKW minted per work unit in the first epoch (0.001 ZKW/WU, WAD-scaled).
    uint256 public constant INITIAL_RATE = 1e15;
    /// Work units per halving. Cumulative work is WAD-scaled, so is this.
    uint256 public constant EPOCH_WORK = 5e11 * WAD;
    /// The geometric series 2 * EPOCH_WORK * INITIAL_RATE, exactly.
    uint256 public constant MAX_SUPPLY = 1e9 * WAD;
    /// A halving past this point mints nothing; stop rather than underflow.
    uint256 internal constant MAX_EPOCH = 128;

    // ── the meter ────────────────────────────────────────────────────────
    /// Public inputs are worth this many constraints each. They cost the
    /// verifier a scalar multiplication apiece, which is not nothing.
    uint256 public constant INPUT_WEIGHT = 8;
    /// Floor added to every circuit: a proof has a fixed cost to check
    /// regardless of how trivial the statement is.
    uint256 public constant BASE_CONSTRAINTS = 1024;
    /// log2 of the reference circuit (2^20 constraints), WAD-scaled. Work is
    /// normalised against it, so a 1M-constraint circuit is ~1M work units.
    uint256 public constant LG_REF = 20 * WAD;

    enum System { GROTH16, PLONK, FFLONK }
    enum Curve { BN254, BLS12_381 }

    // ── the curve ────────────────────────────────────────────────────────
    /// Ask price at zero supply, WAD-scaled USD ($0.001).
    uint256 public constant BASE_PRICE = 1e15;
    /// Added ask per whole ZKW in supply, WAD-scaled USD (1e-9 $/ZKW).
    uint256 public constant PRICE_SLOPE = 1e9;

    // ── the bond ─────────────────────────────────────────────────────────
    /// Share of a claim's value that vests only after the challenge window,
    /// because it rests on the declared constraint count. In basis points.
    uint256 public constant DECLARED_BPS = 8000;
    /// How long an attestor has to contradict a declaration.
    uint256 public constant CHALLENGE_WINDOW = 1 days;

    // ── ERC-20 ───────────────────────────────────────────────────────────
    string public constant name = "ZKNet Work";
    string public constant symbol = "ZKW";
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    // ── state ────────────────────────────────────────────────────────────
    struct Circuit {
        uint256 constraints;   // declared on first claim, then binding
        uint256 publicInputs;  // len(IC) - 1, read off the key
        System system;
        Curve curve;
        address declarer;
        uint64 declaredAt;
        bool corrected;        // an attestor has repriced this circuit
    }

    /// A vesting claim carries everything needed to re-price itself, because
    /// the circuit it rests on can be corrected before it comes due.
    struct Vesting {
        address to;
        uint256 amount;        // vested at the declared size
        uint256 constraints;   // the size it was priced at
        uint256 publicInputs;
        uint256 rate;          // the epoch rate it was minted at
        System system;
        Curve curve;
        uint64 releaseAt;
        bytes32 circuit;
        bool released;
    }

    /// Cumulative work ever metered, WAD-scaled. Drives the halvings.
    uint256 public cumulativeWork;
    /// USD backing the token, WAD-scaled. Only fund() moves it up.
    uint256 public reserve;
    /// The address allowed to contradict a declared constraint count.
    address public attestor;

    mapping(bytes32 => Circuit) public circuits;
    /// keccak of the proof itself: a proof mints exactly once, ever.
    mapping(bytes32 => bool) public spent;
    mapping(uint256 => Vesting) public vesting;
    uint256 public vestingCount;

    event Claimed(
        bytes32 indexed circuit,
        bytes32 indexed nullifier,
        address indexed to,
        uint256 work,
        uint256 minted,
        uint256 vested,
        uint256 usd
    );
    event CircuitDeclared(bytes32 indexed circuit, uint256 constraints, address declarer);
    event CircuitCorrected(bytes32 indexed circuit, uint256 from, uint256 to, address by);
    event Funded(address indexed from, uint256 usd);
    event Redeemed(address indexed from, uint256 amount, uint256 usd);

    // ── types ────────────────────────────────────────────────────────────

    struct VerifyingKey {
        uint256[2] alpha1;
        uint256[2][2] beta2;
        uint256[2][2] gamma2;
        uint256[2][2] delta2;
        uint256[2][] ic;
    }

    struct Proof {
        uint256[2] a;
        uint256[2][2] b;
        uint256[2] c;
    }

    struct Claim {
        Proof proof;
        VerifyingKey vk;
        uint256[] input;       // public signals, in IC order
        uint256 constraints;   // declared; ignored if the circuit is known
        System system;
        Curve curve;
        address to;
    }

    struct Receipt {
        bool verified;
        bytes32 circuit;
        bytes32 nullifier;
        uint256 constraints;   // what the contract used, not what you asked for
        uint256 publicInputs;
        uint256 work;          // WAD-scaled work units
        uint256 minted;        // wei ZKW
        uint256 liquid;        // spendable now
        uint256 vested;        // spendable after the challenge window
        uint256 epoch;
        uint256 rate;
        uint256 askPriceUsd;   // WAD-scaled USD per ZKW, the curve's ask
        uint256 usd;           // minted * ask, WAD-scaled
        uint256 redeemUsd;     // what the reserve would actually pay out
        bool bound;            // constraints came from a prior binding
    }

    constructor(address attestor_) {
        attestor = attestor_;
    }

    // ── the meter ────────────────────────────────────────────────────────

    /**
     * Work units for a circuit, WAD-scaled.
     *
     *   C  = constraints + 8 * publicInputs + 1024
     *   W  = C * log2(C) / log2(2^20) * systemMul * curveMul
     *
     * Superlinear in the constraint count because proving is: the FFTs are
     * O(C log C) and dominate at every size anyone actually deploys. The
     * normalisation is chosen so a 2^20-constraint groth16 circuit on bn254
     * meters at exactly 2^20 work units, which makes the unit legible.
     */
    function measure(
        uint256 constraints,
        uint256 publicInputs,
        System system,
        Curve curve
    ) public pure returns (uint256) {
        uint256 c = constraints + INPUT_WEIGHT * publicInputs + BASE_CONSTRAINTS;
        uint256 work = (c * log2Wad(c * WAD)) / LG_REF;
        work = (work * systemMultiplier(system)) / WAD;
        work = (work * curveMultiplier(curve)) / WAD;
        return work;
    }

    /// What each proof system costs a verifier, relative to groth16.
    function systemMultiplier(System system) public pure returns (uint256) {
        if (system == System.GROTH16) return WAD;
        if (system == System.PLONK) return 135e16;
        return 15e17; // fflonk
    }

    /// Bigger field, more work — bls12-381 arithmetic is ~1.6x bn254.
    function curveMultiplier(Curve curve) public pure returns (uint256) {
        return curve == Curve.BN254 ? WAD : 16e17;
    }

    // ── emission ─────────────────────────────────────────────────────────

    /// Which halving the given cumulative work falls in.
    function epochOf(uint256 cumulative) public pure returns (uint256) {
        return cumulative / EPOCH_WORK;
    }

    /// ZKW per work unit at that epoch, WAD-scaled.
    function rateAt(uint256 epoch) public pure returns (uint256) {
        if (epoch >= MAX_EPOCH) return 0;
        return INITIAL_RATE >> epoch;
    }

    /**
     * What `work` mints, given the network's history.
     *
     * The rate is read once, at the epoch the claim starts in — a claim that
     * straddles a halving is paid at the pre-halving rate rather than split.
     * That is a rounding decision, not an accident: splitting would make the
     * mint depend on the order transactions land in a block.
     */
    function emissionFor(
        uint256 work,
        uint256 cumulative,
        uint256 supply
    ) public pure returns (uint256) {
        uint256 minted = (work * rateAt(epochOf(cumulative))) / WAD;
        if (supply >= MAX_SUPPLY) return 0;
        if (supply + minted > MAX_SUPPLY) return MAX_SUPPLY - supply;
        return minted;
    }

    // ── the curve ────────────────────────────────────────────────────────

    /// The contract's ask for the next token, WAD-scaled USD.
    function askPrice(uint256 supply) public pure returns (uint256) {
        return BASE_PRICE + (supply * PRICE_SLOPE) / WAD;
    }

    /// What burning `amount` would actually pay, WAD-scaled USD. Zero until
    /// the reserve is funded — the ask is a quote, this is the floor.
    function redeemValue(uint256 amount) public view returns (uint256) {
        if (totalSupply == 0) return 0;
        return (reserve * amount) / totalSupply;
    }

    // ── verification ─────────────────────────────────────────────────────

    uint256 internal constant Q =
        21888242871839275222246405745257275088696311157297823662689037894645226208583;
    uint256 internal constant R =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    /**
     * The groth16 pairing check:
     *
     *   e(-A, B) * e(alpha, beta) * e(vk_x, gamma) * e(C, delta) == 1
     *
     * with vk_x = IC[0] + sum(input[i] * IC[i+1]). Four pairings in one call
     * to the 0x08 precompile, so the final exponentiation is paid once.
     */
    function verifyProof(
        Proof calldata proof,
        VerifyingKey calldata vk,
        uint256[] calldata input
    ) public view returns (bool) {
        if (vk.ic.length != input.length + 1) return false;

        uint256[2] memory vkX = vk.ic[0];
        for (uint256 i = 0; i < input.length; i++) {
            if (input[i] >= R) return false;
            vkX = ecAdd(vkX, ecMul(vk.ic[i + 1], input[i]));
        }

        uint256[24] memory buf;
        _put(buf, 0, negate(proof.a), proof.b);
        _put(buf, 6, vk.alpha1, vk.beta2);
        _put(buf, 12, vkX, vk.gamma2);
        _put(buf, 18, proof.c, vk.delta2);

        uint256[1] memory out;
        bool ok;
        assembly {
            ok := staticcall(gas(), 0x08, buf, 768, out, 0x20)
        }
        return ok && out[0] == 1;
    }

    function _put(
        uint256[24] memory buf,
        uint256 slot,
        uint256[2] memory g1,
        uint256[2][2] memory g2
    ) internal pure {
        buf[slot] = g1[0];
        buf[slot + 1] = g1[1];
        buf[slot + 2] = g2[0][0];
        buf[slot + 3] = g2[0][1];
        buf[slot + 4] = g2[1][0];
        buf[slot + 5] = g2[1][1];
    }

    function negate(uint256[2] memory p) internal pure returns (uint256[2] memory) {
        if (p[0] == 0 && p[1] == 0) return p;
        return [p[0], Q - (p[1] % Q)];
    }

    function ecAdd(uint256[2] memory a, uint256[2] memory b)
        internal
        view
        returns (uint256[2] memory r)
    {
        uint256[4] memory in_ = [a[0], a[1], b[0], b[1]];
        bool ok;
        assembly {
            ok := staticcall(gas(), 0x06, in_, 128, r, 64)
        }
        require(ok, "ecAdd");
    }

    function ecMul(uint256[2] memory p, uint256 s)
        internal
        view
        returns (uint256[2] memory r)
    {
        uint256[3] memory in_ = [p[0], p[1], s];
        bool ok;
        assembly {
            ok := staticcall(gas(), 0x07, in_, 96, r, 64)
        }
        require(ok, "ecMul");
    }

    // ── identity ─────────────────────────────────────────────────────────

    /// A circuit is its verification key. Nothing else identifies it, and
    /// nothing about it can be changed without changing this.
    function circuitDigest(VerifyingKey calldata vk) public pure returns (bytes32) {
        return keccak256(abi.encode(vk.alpha1, vk.beta2, vk.gamma2, vk.delta2, vk.ic));
    }

    /// A proof mints once. This is what `spent` is keyed on.
    function nullifier(Proof calldata p, uint256[] calldata input)
        public
        pure
        returns (bytes32)
    {
        return keccak256(abi.encode(p.a, p.b, p.c, input));
    }

    // ── claiming ─────────────────────────────────────────────────────────

    /**
     * Verify, meter, mint.
     *
     * Reverts on a bad proof, on a proof already spent, and on a declared
     * constraint count that disagrees with what this circuit was bound to.
     * DECLARED_BPS of the mint vests at the end of the challenge window; the
     * rest is liquid immediately, because it rests on nothing the caller told
     * us. A brand-new circuit's declaration is what CircuitDeclared announces
     * — that event is the attestor's cue to go read the .r1cs.
     */
    function claim(Claim calldata c) external returns (Receipt memory) {
        return _claim(c, true);
    }

    /**
     * The whole of claim(), executed and then thrown away.
     *
     * This is not a second implementation — it is claim()'s body with the
     * revert removed, so an eth_call can run the real thing against a real
     * EVM and read back what it would have done. That is how this contract is
     * exercised without being deployed: `zknet simulate` injects the runtime
     * bytecode at an unused address on a public node and calls this.
     */
    function simulateClaim(Claim calldata c) external returns (Receipt memory) {
        return _claim(c, false);
    }

    /// The same code path, read-only, for pricing a claim before making it.
    function quote(Claim calldata c) external view returns (Receipt memory r) {
        r.verified = verifyProof(c.proof, c.vk, c.input);
        r.circuit = circuitDigest(c.vk);
        r.nullifier = nullifier(c.proof, c.input);
        r.publicInputs = c.vk.ic.length - 1;

        Circuit storage known = circuits[r.circuit];
        r.bound = known.declaredAt != 0;
        r.constraints = r.bound ? known.constraints : c.constraints;

        _price(r, c.system, c.curve);
    }

    function _claim(Claim calldata c, bool strict) internal returns (Receipt memory r) {
        r.verified = verifyProof(c.proof, c.vk, c.input);
        r.circuit = circuitDigest(c.vk);
        r.nullifier = nullifier(c.proof, c.input);
        r.publicInputs = c.vk.ic.length - 1;

        // A proof that does not verify mints nothing either way. strict is
        // only about how the caller hears it: claim() reverts, simulateClaim()
        // hands back the receipt so the reason is legible.
        if (!r.verified) {
            require(!strict, "ZKNet: proof does not verify");
            return r;
        }
        require(!spent[r.nullifier], "ZKNet: proof already claimed");

        Circuit storage known = circuits[r.circuit];
        if (known.declaredAt == 0) {
            require(c.constraints > 0, "ZKNet: declare the constraint count");
            circuits[r.circuit] = Circuit({
                constraints: c.constraints,
                publicInputs: r.publicInputs,
                system: c.system,
                curve: c.curve,
                declarer: msg.sender,
                declaredAt: uint64(block.timestamp),
                corrected: false
            });
            r.constraints = c.constraints;
            emit CircuitDeclared(r.circuit, c.constraints, msg.sender);
        } else {
            // A circuit's size is fixed by its first claim. Passing a
            // different number is a lie about a circuit the chain has
            // already seen, so it is rejected rather than quietly ignored.
            require(
                c.constraints == 0 || c.constraints == known.constraints,
                "ZKNet: constraints disagree with this circuit"
            );
            r.constraints = known.constraints;
            r.bound = true;
        }

        _price(r, c.system, c.curve);

        spent[r.nullifier] = true;
        cumulativeWork += r.work;

        address to = c.to == address(0) ? msg.sender : c.to;
        r.vested = (r.minted * DECLARED_BPS) / 10000;
        r.liquid = r.minted - r.vested;

        _mint(to, r.liquid);
        if (r.vested > 0) {
            _mint(address(this), r.vested);
            vesting[vestingCount++] = Vesting({
                to: to,
                amount: r.vested,
                constraints: r.constraints,
                publicInputs: r.publicInputs,
                rate: r.rate,
                system: c.system,
                curve: c.curve,
                releaseAt: uint64(block.timestamp) + uint64(CHALLENGE_WINDOW),
                circuit: r.circuit,
                released: false
            });
        }

        emit Claimed(r.circuit, r.nullifier, to, r.work, r.minted, r.vested, r.usd);
    }

    function _price(Receipt memory r, System system, Curve curve) internal view {
        r.work = measure(r.constraints, r.publicInputs, system, curve);
        r.epoch = epochOf(cumulativeWork);
        r.rate = rateAt(r.epoch);
        r.minted = emissionFor(r.work, cumulativeWork, totalSupply);
        r.askPriceUsd = askPrice(totalSupply);
        r.usd = (r.minted * r.askPriceUsd) / WAD;
        r.redeemUsd = redeemValue(r.minted);
    }

    /// Hand over vested ZKW once its challenge window has closed. A circuit
    /// corrected downward in the meantime pays out at the corrected size and
    /// the difference is burned — it was never earned.
    function release(uint256 id) external returns (uint256 paid) {
        Vesting storage v = vesting[id];
        require(v.releaseAt != 0, "ZKNet: no such vesting");
        require(!v.released, "ZKNet: already released");
        require(block.timestamp >= v.releaseAt, "ZKNet: still in the window");
        v.released = true;

        paid = v.amount;
        if (circuits[v.circuit].corrected) {
            uint256 work = measure(
                circuits[v.circuit].constraints, v.publicInputs, v.system, v.curve
            );
            uint256 minted = (work * v.rate) / WAD;
            uint256 repriced = (minted * DECLARED_BPS) / 10000;
            paid = repriced < v.amount ? repriced : v.amount;
        }

        balanceOf[address(this)] -= v.amount;
        if (paid > 0) {
            balanceOf[v.to] += paid;
            emit Transfer(address(this), v.to, paid);
        }
        if (v.amount > paid) {
            uint256 burned = v.amount - paid;
            totalSupply -= burned;
            emit Transfer(address(this), address(0), burned);
        }
    }

    /// The attestor has read the .r1cs and it does not say what the declarer
    /// said it says. Reprice the circuit; everything vesting against it pays
    /// out at the corrected size.
    function correct(bytes32 circuit, uint256 constraints) external {
        require(msg.sender == attestor, "ZKNet: not the attestor");
        Circuit storage k = circuits[circuit];
        require(k.declaredAt != 0, "ZKNet: unknown circuit");
        require(constraints < k.constraints, "ZKNet: corrections only go down");
        emit CircuitCorrected(circuit, k.constraints, constraints, msg.sender);
        k.constraints = constraints;
        k.corrected = true;
    }

    // ── reserve ──────────────────────────────────────────────────────────

    /// Back the token. In a deployment this takes a stablecoin transfer; here
    /// it takes the amount, because the point of the accounting is the same
    /// either way and the module runs it against a local ledger too.
    function fund(uint256 usd) external {
        reserve += usd;
        emit Funded(msg.sender, usd);
    }

    /// Burn ZKW for its share of the reserve.
    function redeem(uint256 amount) external returns (uint256 usd) {
        require(balanceOf[msg.sender] >= amount, "ZKNet: balance");
        usd = redeemValue(amount);
        balanceOf[msg.sender] -= amount;
        totalSupply -= amount;
        reserve -= usd;
        emit Transfer(msg.sender, address(0), amount);
        emit Redeemed(msg.sender, amount, usd);
    }

    // ── ERC-20 ───────────────────────────────────────────────────────────

    function _mint(address to, uint256 amount) internal {
        if (amount == 0) return;
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount)
        external
        returns (bool)
    {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) allowance[from][msg.sender] = allowed - amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
        return true;
    }

    // ── math ─────────────────────────────────────────────────────────────

    /**
     * log2 of a WAD-scaled x >= WAD, WAD-scaled.
     *
     * Integer part from the most significant bit, then one bit of the
     * fraction per squaring — the standard fixed-point method. Written out
     * here rather than pulled from a library because src/meter.py has to
     * reproduce it operation for operation, and a test asserts they agree.
     */
    function log2Wad(uint256 x) public pure returns (uint256 result) {
        require(x >= WAD, "log2Wad: x < 1");
        uint256 n = msb(x / WAD);
        result = n * WAD;

        uint256 y = x >> n;
        if (y == WAD) return result;

        for (uint256 delta = WAD / 2; delta > 0; delta >>= 1) {
            y = (y * y) / WAD;
            if (y >= 2 * WAD) {
                result += delta;
                y >>= 1;
            }
        }
    }

    /// Index of the most significant set bit; msb(1) == 0.
    function msb(uint256 x) public pure returns (uint256 n) {
        require(x > 0, "msb: zero");
        while (x > 1) {
            x >>= 1;
            n++;
        }
    }
}
