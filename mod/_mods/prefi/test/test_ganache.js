// Test script for Ganache local blockchain
const Web3 = require('web3');
const fs = require('fs');
const solc = require('solc');

// Connect to Ganache (default: http://localhost:8545)
const web3 = new Web3('http://localhost:8545');

async function deployAndTest() {
    console.log('\n=== Prediction Market Smart Contract Test on Ganache ===\n');
    
    // Get accounts
    const accounts = await web3.eth.getAccounts();
    console.log('Available accounts:', accounts.length);
    console.log('Deployer account:', accounts[0]);
    
    // Read and compile contract
    const contractPath = '../contracts/PredictionMarket.sol';
    const source = fs.readFileSync(contractPath, 'utf8');
    
    const input = {
        language: 'Solidity',
        sources: {
            'PredictionMarket.sol': {
                content: source
            }
        },
        settings: {
            outputSelection: {
                '*': {
                    '*': ['*']
                }
            }
        }
    };
    
    console.log('\nCompiling contract...');
    const output = JSON.parse(solc.compile(JSON.stringify(input)));
    const contract = output.contracts['PredictionMarket.sol']['PredictionMarket'];
    const abi = contract.abi;
    const bytecode = contract.evm.bytecode.object;
    
    // Deploy contract
    console.log('Deploying contract...');
    const PredictionMarket = new web3.eth.Contract(abi);
    const deployed = await PredictionMarket.deploy({ data: '0x' + bytecode })
        .send({ from: accounts[0], gas: 5000000 });
    
    console.log('Contract deployed at:', deployed.options.address);
    
    // Test 1: Deposit funds
    console.log('\n--- Test 1: Deposit Funds ---');
    await deployed.methods.deposit().send({ 
        from: accounts[0], 
        value: web3.utils.toWei('10', 'ether') 
    });
    const balance = await deployed.methods.getBalance(accounts[0]).call();
    console.log('User balance:', web3.utils.fromWei(balance, 'ether'), 'ETH');
    
    // Test 2: Open position
    console.log('\n--- Test 2: Open Position ---');
    const tx = await deployed.methods.openPosition(
        web3.utils.toWei('1', 'ether'),
        0, // Direction.UP
        50000, // Entry price (e.g., BTC at $50,000)
        'BTC'
    ).send({ from: accounts[0], gas: 500000 });
    
    const positionId = tx.events.PositionOpened.returnValues.positionId;
    console.log('Position opened:', positionId);
    
    // Test 3: Get position details
    console.log('\n--- Test 3: Position Details ---');
    const position = await deployed.methods.getPosition(positionId).call();
    console.log('User:', position.user);
    console.log('Amount:', web3.utils.fromWei(position.amount, 'ether'), 'ETH');
    console.log('Direction:', position.direction === '0' ? 'UP' : 'DOWN');
    console.log('Entry Price:', position.entryPrice);
    console.log('Asset:', position.asset);
    
    // Test 4: Fast forward time (Ganache feature)
    console.log('\n--- Test 4: Fast Forward 24 Hours ---');
    await web3.currentProvider.send({
        jsonrpc: '2.0',
        method: 'evm_increaseTime',
        params: [24 * 60 * 60], // 24 hours in seconds
        id: new Date().getTime()
    }, () => {});
    await web3.currentProvider.send({
        jsonrpc: '2.0',
        method: 'evm_mine',
        params: [],
        id: new Date().getTime()
    }, () => {});
    console.log('Time advanced by 24 hours');
    
    // Test 5: Settle position
    console.log('\n--- Test 5: Settle Position ---');
    await deployed.methods.settlePosition(
        positionId,
        55000 // Exit price (profitable - went UP)
    ).send({ from: accounts[0], gas: 500000 });
    console.log('Position settled');
    
    const isProfitable = await deployed.methods.isProfitable(positionId).call();
    console.log('Is profitable:', isProfitable);
    
    // Test 6: Distribute rewards
    console.log('\n--- Test 6: Distribute Rewards ---');
    await deployed.methods.distributeRewards('BTC').send({ 
        from: accounts[0], 
        gas: 500000 
    });
    console.log('Rewards distributed');
    
    const finalBalance = await deployed.methods.getBalance(accounts[0]).call();
    console.log('Final balance:', web3.utils.fromWei(finalBalance, 'ether'), 'ETH');
    
    console.log('\n=== All Tests Completed Successfully ===\n');
}

deployAndTest().catch(console.error);