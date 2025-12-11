// Test script for Base network (testnet)
const Web3 = require('web3');
const fs = require('fs');
const solc = require('solc');

// Connect to Base Goerli testnet
// Get RPC URL from: https://docs.base.org/network-information
const BASE_GOERLI_RPC = 'https://goerli.base.org';
const web3 = new Web3(BASE_GOERLI_RPC);

// Your private key (NEVER commit this to git!)
const PRIVATE_KEY = process.env.PRIVATE_KEY || 'YOUR_PRIVATE_KEY_HERE';
const account = web3.eth.accounts.privateKeyToAccount(PRIVATE_KEY);
web3.eth.accounts.wallet.add(account);

async function deployAndTest() {
    console.log('\n=== Prediction Market Smart Contract Test on Base Goerli ===\n');
    console.log('Deployer address:', account.address);
    
    // Check balance
    const balance = await web3.eth.getBalance(account.address);
    console.log('Balance:', web3.utils.fromWei(balance, 'ether'), 'ETH');
    
    if (balance === '0') {
        console.log('\n⚠️  No ETH balance. Get testnet ETH from Base Goerli faucet:');
        console.log('https://www.coinbase.com/faucets/base-ethereum-goerli-faucet');
        return;
    }
    
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
    console.log('Deploying contract to Base Goerli...');
    const PredictionMarket = new web3.eth.Contract(abi);
    
    const gasEstimate = await PredictionMarket.deploy({ data: '0x' + bytecode })
        .estimateGas({ from: account.address });
    
    const deployed = await PredictionMarket.deploy({ data: '0x' + bytecode })
        .send({ 
            from: account.address, 
            gas: gasEstimate + 100000,
            gasPrice: await web3.eth.getGasPrice()
        });
    
    console.log('✅ Contract deployed at:', deployed.options.address);
    console.log('View on BaseScan:', `https://goerli.basescan.org/address/${deployed.options.address}`);
    
    // Test 1: Deposit funds
    console.log('\n--- Test 1: Deposit Funds ---');
    await deployed.methods.deposit().send({ 
        from: account.address, 
        value: web3.utils.toWei('0.01', 'ether'),
        gas: 100000
    });
    const userBalance = await deployed.methods.getBalance(account.address).call();
    console.log('User balance:', web3.utils.fromWei(userBalance, 'ether'), 'ETH');
    
    // Test 2: Open position
    console.log('\n--- Test 2: Open Position ---');
    const tx = await deployed.methods.openPosition(
        web3.utils.toWei('0.001', 'ether'),
        0, // Direction.UP
        50000, // Entry price
        'BTC'
    ).send({ from: account.address, gas: 500000 });
    
    const positionId = tx.events.PositionOpened.returnValues.positionId;
    console.log('✅ Position opened:', positionId);
    console.log('View transaction:', `https://goerli.basescan.org/tx/${tx.transactionHash}`);
    
    // Test 3: Get position details
    console.log('\n--- Test 3: Position Details ---');
    const position = await deployed.methods.getPosition(positionId).call();
    console.log('Amount:', web3.utils.fromWei(position.amount, 'ether'), 'ETH');
    console.log('Direction:', position.direction === '0' ? 'UP' : 'DOWN');
    console.log('Entry Price:', position.entryPrice);
    console.log('Exit Time:', new Date(position.exitTime * 1000).toISOString());
    
    console.log('\n=== Deployment and Initial Tests Completed ===');
    console.log('\n📝 Next steps:');
    console.log('1. Wait 24 hours for position to mature');
    console.log('2. Call settlePosition() with exit price');
    console.log('3. Call distributeRewards() to claim profits');
    console.log('\nContract address:', deployed.options.address);
}

deployAndTest().catch(console.error);