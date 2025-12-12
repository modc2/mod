// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../libraries/BlocStorage.sol";
import "../interfaces/IBlocStaking.sol";

library BlocRegistry {
    event BlocRegistered(uint256 indexed blocId, address indexed creator, string ipfsHash);
    event BlocUpdated(uint256 indexed blocId, string newIpfsHash);
    event BlocDeregistered(uint256 indexed blocId);
    event BlocTransferred(uint256 indexed blocId, address indexed from, address indexed to);

    function registerBloc(string memory ipfsHash) internal returns (uint256) {
        require(bytes(ipfsHash).length > 0, "IPFS hash cannot be empty");
        
        BlocStorage.Layout storage s = BlocStorage.layout();
        uint256 blocId = s.nextBlocId++;
        s.blocs[blocId] = IBlocStaking.Bloc({
            creator: msg.sender,
            ipfsHash: ipfsHash,
            totalStaked: 0,
            active: true
        });
        
        emit BlocRegistered(blocId, msg.sender, ipfsHash);
        return blocId;
    }

    function updateBloc(uint256 blocId, string memory newIpfsHash) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        require(s.blocs[blocId].active, "Bloc not active");
        require(s.blocs[blocId].creator == msg.sender, "Only creator can update");
        require(bytes(newIpfsHash).length > 0, "IPFS hash cannot be empty");
        
        s.blocs[blocId].ipfsHash = newIpfsHash;
        emit BlocUpdated(blocId, newIpfsHash);
    }

    function transferBloc(uint256 blocId, address newCreator) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        require(s.blocs[blocId].active, "Bloc not active");
        require(s.blocs[blocId].creator == msg.sender, "Only creator can transfer");
        require(newCreator != address(0), "Invalid new creator");
        
        address oldCreator = s.blocs[blocId].creator;
        s.blocs[blocId].creator = newCreator;
        emit BlocTransferred(blocId, oldCreator, newCreator);
    }

    function deregisterBloc(uint256 blocId) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        require(s.blocs[blocId].active, "Bloc not active");
        require(s.blocs[blocId].creator == msg.sender, "Only creator can deregister");
        require(s.blocs[blocId].totalStaked == 0, "Cannot deregister with active stakes");
        
        s.blocs[blocId].active = false;
        emit BlocDeregistered(blocId);
    }
}
