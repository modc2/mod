// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title NFT — a minimal ERC-721 with sequential ids and per-token URIs.
/// @notice Implements the 721 core plus metadata, and the safeTransfer receiver
/// check, because a collection that can send a token into a contract that
/// cannot hold one is a collection that loses tokens.
contract NFT {
    string public name;
    string public symbol;
    address public owner;
    uint256 public totalSupply;
    uint256 public mintPrice;      // wei; 0 = free
    bool public publicMint;

    mapping(uint256 => address) internal _ownerOf;
    mapping(address => uint256) public balanceOf;
    mapping(uint256 => address) public getApproved;
    mapping(address => mapping(address => bool)) public isApprovedForAll;
    mapping(uint256 => string) internal _tokenURI;

    event Transfer(address indexed from, address indexed to, uint256 indexed id);
    event Approval(address indexed owner, address indexed spender, uint256 indexed id);
    event ApprovalForAll(address indexed owner, address indexed operator, bool approved);

    error NotOwner();
    error NotAuthorized();
    error NoSuchToken();
    error WrongPayment();
    error NotOpen();
    error UnsafeRecipient();

    constructor(string memory _name, string memory _symbol, uint256 _mintPrice, bool _publicMint) {
        name = _name;
        symbol = _symbol;
        owner = msg.sender;
        mintPrice = _mintPrice;
        publicMint = _publicMint;
    }

    function ownerOf(uint256 id) public view returns (address held) {
        held = _ownerOf[id];
        if (held == address(0)) revert NoSuchToken();
    }

    function tokenURI(uint256 id) external view returns (string memory) {
        ownerOf(id);
        return _tokenURI[id];
    }

    function supportsInterface(bytes4 id) external pure returns (bool) {
        return id == 0x01ffc9a7 || id == 0x80ac58cd || id == 0x5b5e139f;
    }

    /// @notice Mint the next id. Open to anyone when publicMint is on and the
    /// price is paid; otherwise the deployer only.
    function mint(address to, string calldata uri) external payable returns (uint256 id) {
        if (msg.sender != owner) {
            if (!publicMint) revert NotOpen();
            if (msg.value != mintPrice) revert WrongPayment();
        }
        id = ++totalSupply;
        _ownerOf[id] = to;
        balanceOf[to] += 1;
        _tokenURI[id] = uri;
        emit Transfer(address(0), to, id);
    }

    function setTokenURI(uint256 id, string calldata uri) external {
        if (msg.sender != owner) revert NotOwner();
        ownerOf(id);
        _tokenURI[id] = uri;
    }

    function setSale(uint256 price, bool open) external {
        if (msg.sender != owner) revert NotOwner();
        mintPrice = price;
        publicMint = open;
    }

    function withdraw(address payable to) external {
        if (msg.sender != owner) revert NotOwner();
        (bool ok, ) = to.call{value: address(this).balance}("");
        require(ok, "withdraw failed");
    }

    function approve(address spender, uint256 id) external {
        address holder = ownerOf(id);
        if (msg.sender != holder && !isApprovedForAll[holder][msg.sender]) revert NotAuthorized();
        getApproved[id] = spender;
        emit Approval(holder, spender, id);
    }

    function setApprovalForAll(address operator, bool approved) external {
        isApprovedForAll[msg.sender][operator] = approved;
        emit ApprovalForAll(msg.sender, operator, approved);
    }

    function transferFrom(address from, address to, uint256 id) public {
        if (from != ownerOf(id)) revert NotAuthorized();
        if (to == address(0)) revert NotAuthorized();
        if (msg.sender != from && msg.sender != getApproved[id]
            && !isApprovedForAll[from][msg.sender]) revert NotAuthorized();
        balanceOf[from] -= 1;
        balanceOf[to] += 1;
        _ownerOf[id] = to;
        delete getApproved[id];
        emit Transfer(from, to, id);
    }

    function safeTransferFrom(address from, address to, uint256 id) public {
        safeTransferFrom(from, to, id, "");
    }

    function safeTransferFrom(address from, address to, uint256 id, bytes memory data) public {
        transferFrom(from, to, id);
        if (to.code.length != 0) {
            (bool ok, bytes memory ret) = to.call(
                abi.encodeWithSelector(0x150b7a02, msg.sender, from, id, data));
            if (!ok || ret.length < 32 || abi.decode(ret, (bytes4)) != bytes4(0x150b7a02))
                revert UnsafeRecipient();
        }
    }
}
