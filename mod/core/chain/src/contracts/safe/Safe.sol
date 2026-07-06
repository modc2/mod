// SPDX-License-Identifier: LGPL-3.0-only
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "./ISafe.sol";
import "./Enum.sol";

/**
 * @title Safe - A multisignature wallet with support for confirmations using signed messages.
 * @dev Most important concepts:
 *      - Threshold: Number of required confirmations for a Safe transaction
 *      - Owners: List of addresses that can confirm Safe transactions
 *      - Nonce: Counter for replay protection
 */
contract Safe is ISafe {
    using ECDSA for bytes32;

    string public constant VERSION = "1.0.0";

    // Storage
    address internal singleton;
    mapping(address => address) internal owners;
    uint256 internal ownerCount;
    uint256 internal threshold;

    // Nonce for replay protection
    uint256 public nonce;

    // Domain separator for EIP-712
    bytes32 private _DOMAIN_SEPARATOR;

    // keccak256(
    //     "EIP712Domain(uint256 chainId,address verifyingContract)"
    // );
    bytes32 private constant DOMAIN_SEPARATOR_TYPEHASH = 0x47e79534a245952e8b16893a336b85a3d9ea9fa8c573f3d803afb92a79469218;

    // keccak256(
    //     "SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)"
    // );
    bytes32 private constant SAFE_TX_TYPEHASH = 0xbb8310d486368db6bd6f849402fdd73ad53d316b5a4b2644ad6efe0f941286d8;

    address internal constant SENTINEL_OWNERS = address(0x1);

    event SafeSetup(address indexed initiator, address[] owners, uint256 threshold, address initializer, address fallbackHandler);
    event ApproveHash(bytes32 indexed approvedHash, address indexed owner);
    event SignMsg(bytes32 indexed msgHash);
    event ExecutionFailure(bytes32 txHash, uint256 payment);
    event ExecutionSuccess(bytes32 txHash, uint256 payment);

    // Modifiers
    modifier authorized() {
        require(msg.sender == address(this), "Safe: Not authorized");
        _;
    }

    /**
     * @dev Setup function sets initial storage of contract.
     * @param _owners List of Safe owners.
     * @param _threshold Number of required confirmations for a Safe transaction.
     * @param to Contract address for optional delegate call.
     * @param data Data payload for optional delegate call.
     * @param fallbackHandler Handler for fallback calls to this contract
     * @param paymentToken Token that should be used for the payment (0 is ETH)
     * @param payment Value that should be paid
     * @param paymentReceiver Address that should receive the payment (or 0 if tx.origin)
     */
    function setup(
        address[] calldata _owners,
        uint256 _threshold,
        address to,
        bytes calldata data,
        address fallbackHandler,
        address paymentToken,
        uint256 payment,
        address payable paymentReceiver
    ) external override {
        require(threshold == 0, "Safe: Already initialized");
        require(_threshold > 0, "Safe: Threshold must be > 0");
        require(_threshold <= _owners.length, "Safe: Threshold exceeds owner count");
        require(_owners.length <= 10, "Safe: Too many owners");

        // Set domain separator
        _DOMAIN_SEPARATOR = keccak256(
            abi.encode(DOMAIN_SEPARATOR_TYPEHASH, block.chainid, address(this))
        );

        // Initialize owners - check for duplicates first
        for (uint256 i = 0; i < _owners.length; i++) {
            address owner = _owners[i];
            require(owner != address(0) && owner != SENTINEL_OWNERS && owner != address(this), "Safe: Invalid owner");
            for (uint256 j = i + 1; j < _owners.length; j++) {
                require(_owners[i] != _owners[j], "Safe: Duplicate owner");
            }
        }

        // Set up linked list
        address currentOwner = SENTINEL_OWNERS;
        for (uint256 i = 0; i < _owners.length; i++) {
            address owner = _owners[i];
            owners[currentOwner] = owner;
            currentOwner = owner;
        }
        owners[currentOwner] = SENTINEL_OWNERS;
        ownerCount = _owners.length;
        threshold = _threshold;

        // Optional setup calls
        if (to != address(0)) {
            (bool success, ) = to.delegatecall(data);
            require(success, "Safe: Setup call failed");
        }

        emit SafeSetup(msg.sender, _owners, _threshold, to, fallbackHandler);

        // Handle payment
        if (payment > 0) {
            handlePayment(payment, paymentToken, paymentReceiver);
        }
    }

    /**
     * @dev Executes a Safe transaction confirmed by required number of owners.
     */
    function execTransaction(
        address to,
        uint256 value,
        bytes calldata data,
        Enum.Operation operation,
        uint256 safeTxGas,
        uint256 baseGas,
        uint256 gasPrice,
        address gasToken,
        address payable refundReceiver,
        bytes memory signatures
    ) external payable override returns (bool success) {
        bytes32 txHash;
        {
            bytes memory txHashData = encodeTransactionData(
                to,
                value,
                data,
                operation,
                safeTxGas,
                baseGas,
                gasPrice,
                gasToken,
                refundReceiver,
                nonce
            );
            txHash = keccak256(txHashData);
            checkSignatures(txHash, signatures);
        }

        // Increment nonce before execution
        nonce++;

        // Execute transaction
        uint256 gasUsed = gasleft();
        success = execute(to, value, data, operation, safeTxGas);
        gasUsed = gasUsed - gasleft();

        // Calculate payment
        uint256 payment = 0;
        if (gasPrice > 0) {
            payment = handlePayment(gasUsed + baseGas, gasToken, refundReceiver);
        }

        if (success) {
            emit ExecutionSuccess(txHash, payment);
        } else {
            emit ExecutionFailure(txHash, payment);
        }
    }

    /**
     * @dev Execute transaction helper.
     */
    function execute(
        address to,
        uint256 value,
        bytes memory data,
        Enum.Operation operation,
        uint256 txGas
    ) internal returns (bool success) {
        // If txGas is 0, forward all available gas
        if (txGas == 0) {
            txGas = gasleft();
        }

        if (operation == Enum.Operation.DelegateCall) {
            assembly {
                success := delegatecall(txGas, to, add(data, 0x20), mload(data), 0, 0)
            }
        } else {
            assembly {
                success := call(txGas, to, value, add(data, 0x20), mload(data), 0, 0)
            }
        }
    }

    /**
     * @dev Check signatures.
     */
    function checkSignatures(bytes32 dataHash, bytes memory signatures) public view {
        require(signatures.length >= threshold * 65, "Safe: Not enough signatures");

        address lastOwner = address(0);
        address currentOwner;
        uint8 v;
        bytes32 r;
        bytes32 s;

        for (uint256 i = 0; i < threshold; i++) {
            (v, r, s) = signatureSplit(signatures, i);

            if (v == 0) {
                // Contract signature (not implemented in this basic version)
                revert("Safe: Contract signatures not supported");
            } else if (v == 1) {
                // Approved hash (not implemented in this basic version)
                revert("Safe: Approved hash not supported");
            } else {
                // ECDSA signature
                currentOwner = ecrecover(
                    keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", dataHash)),
                    v,
                    r,
                    s
                );
            }

            require(currentOwner > lastOwner && owners[currentOwner] != address(0), "Safe: Invalid signature order");
            lastOwner = currentOwner;
        }
    }

    /**
     * @dev Split signature.
     */
    function signatureSplit(bytes memory signatures, uint256 pos)
        internal
        pure
        returns (uint8 v, bytes32 r, bytes32 s)
    {
        assembly {
            let signaturePos := mul(0x41, pos)
            r := mload(add(signatures, add(signaturePos, 0x20)))
            s := mload(add(signatures, add(signaturePos, 0x40)))
            v := byte(0, mload(add(signatures, add(signaturePos, 0x60))))
        }
    }

    /**
     * @dev Handle payment.
     */
    function handlePayment(
        uint256 gasUsed,
        address gasToken,
        address payable receiver
    ) private returns (uint256 payment) {
        payment = gasUsed * tx.gasprice;
        address payable paymentReceiver = receiver == address(0) ? payable(tx.origin) : receiver;

        if (gasToken == address(0)) {
            (bool success, ) = paymentReceiver.call{value: payment}("");
            require(success, "Safe: ETH payment failed");
        }
        // ERC20 payment not implemented in this basic version
    }

    /**
     * @dev Encode transaction data.
     */
    function encodeTransactionData(
        address to,
        uint256 value,
        bytes calldata data,
        Enum.Operation operation,
        uint256 safeTxGas,
        uint256 baseGas,
        uint256 gasPrice,
        address gasToken,
        address refundReceiver,
        uint256 _nonce
    ) public view returns (bytes memory) {
        bytes32 safeTxHash = keccak256(
            abi.encode(
                SAFE_TX_TYPEHASH,
                to,
                value,
                keccak256(data),
                operation,
                safeTxGas,
                baseGas,
                gasPrice,
                gasToken,
                refundReceiver,
                _nonce
            )
        );
        return abi.encodePacked(bytes1(0x19), bytes1(0x01), _DOMAIN_SEPARATOR, safeTxHash);
    }

    /**
     * @dev Returns transaction hash to be signed by owners.
     */
    function getTransactionHash(
        address to,
        uint256 value,
        bytes calldata data,
        Enum.Operation operation,
        uint256 safeTxGas,
        uint256 baseGas,
        uint256 gasPrice,
        address gasToken,
        address refundReceiver,
        uint256 _nonce
    ) public view override returns (bytes32) {
        return keccak256(encodeTransactionData(to, value, data, operation, safeTxGas, baseGas, gasPrice, gasToken, refundReceiver, _nonce));
    }

    /**
     * @dev Add a new owner.
     */
    function addOwnerWithThreshold(address owner, uint256 _threshold) public authorized {
        require(owner != address(0) && owner != SENTINEL_OWNERS && owner != address(this), "Safe: Invalid owner");
        require(owners[owner] == address(0), "Safe: Owner already exists");

        owners[owner] = owners[SENTINEL_OWNERS];
        owners[SENTINEL_OWNERS] = owner;
        ownerCount++;

        emit AddedOwner(owner);

        if (_threshold != threshold) {
            changeThreshold(_threshold);
        }
    }

    /**
     * @dev Remove an owner.
     */
    function removeOwner(address prevOwner, address owner, uint256 _threshold) public authorized {
        require(ownerCount - 1 >= _threshold, "Safe: New owner count must be >= threshold");
        require(owner != address(0) && owner != SENTINEL_OWNERS, "Safe: Invalid owner");
        require(owners[prevOwner] == owner, "Safe: Invalid prevOwner/owner pair");

        owners[prevOwner] = owners[owner];
        owners[owner] = address(0);
        ownerCount--;

        emit RemovedOwner(owner);

        if (_threshold != threshold) {
            changeThreshold(_threshold);
        }
    }

    /**
     * @dev Change threshold.
     */
    function changeThreshold(uint256 _threshold) public authorized {
        require(_threshold > 0 && _threshold <= ownerCount, "Safe: Invalid threshold");
        threshold = _threshold;
        emit ChangedThreshold(_threshold);
    }

    /**
     * @dev Returns the current threshold.
     */
    function getThreshold() public view override returns (uint256) {
        return threshold;
    }

    /**
     * @dev Returns if an address is an owner.
     */
    function isOwner(address owner) public view override returns (bool) {
        return owner != SENTINEL_OWNERS && owners[owner] != address(0);
    }

    /**
     * @dev Returns array of owners.
     */
    function getOwners() public view override returns (address[] memory) {
        address[] memory array = new address[](ownerCount);

        uint256 index = 0;
        address currentOwner = owners[SENTINEL_OWNERS];
        while (currentOwner != SENTINEL_OWNERS) {
            array[index] = currentOwner;
            currentOwner = owners[currentOwner];
            index++;
        }
        return array;
    }

    // Events
    event AddedOwner(address owner);
    event RemovedOwner(address owner);
    event ChangedThreshold(uint256 threshold);

    // Receive function
    receive() external payable {}
}
