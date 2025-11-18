import { ApiPromise, WsProvider } from '@polkadot/api'

const networks = [
    { id: 'test', label: 'Modchain Devnet', url: 'wss://dev.api.modchain.ai' },
  ]
const network = 'test'


export class Network 
    {
    public api: ApiPromise | null = null
    public provider: WsProvider | null = null
    public url: string = ''
    constructor( public network: string = 'test' ) { 
        this.setNetwork(network);
    }

    setNetwork(network:string) : string {
        const selectedNetwork = networks.find((n) => n.id === network)
        if (!selectedNetwork) {
            throw new Error(`Network with id '${network}' not found.`)
        }
        this.url = selectedNetwork.url
        this.connect().then(() => {
            console.log(`Connected to ${network} at ${this.url}`)
        }).catch((err) => {
            console.error(`Failed to connect to ${network} at ${this.url}:`, err)
        })
        return this.url;
    }

    async connect() {
        const provider = new WsProvider(this.url)
        const api = await ApiPromise.create({ provider: provider })
        this.provider = provider
        this.api = api
        await this.api.isReady

    }

    async disconnect() {
        if (this.api) {
            await this.api.disconnect()
            this.api = null
            this.provider = null
            console.log('Disconnected from network.')
        }
    }

    async balance(address: string) : Promise<string> {
        await this.connect();
        if (!this.api) {
            throw new Error('API is not connected.')
        }
        const accountInfo: any = await this.api.query.system.account(address)
        const freeBalance = accountInfo.data.free.toBigInt()
        const formattedBalance = Number(freeBalance) / 1e12
        await this.disconnect();
        return formattedBalance.toFixed(4);
    }


    // tear down disconnect 

    async executeTransfer(walletAddress:string, toAddress:string, amount: string)  {

        try {
        const selectedNetwork = networks.find((n) => n.id === network)
        if (!selectedNetwork) throw new Error('Network not found')

        const provider = new WsProvider(selectedNetwork.url)
        const api = await ApiPromise.create({ provider })
        await api.isReady

        const recipientAddress = api.registry.createType(
            'AccountId32',
            toAddress
        ).toString()

        const amountFloat = parseFloat(amount)
        if (isNaN(amountFloat) || amountFloat <= 0)
            throw new Error('Invalid amount')

        const transferAmount = BigInt(Math.floor(amountFloat * 1e12))

        const senderInfo: any = await api.query.system.account(walletAddress)
        const senderBalance = senderInfo.data.free.toBigInt()
        const feeBuffer = BigInt(100_000_000)
        if (senderBalance < transferAmount + feeBuffer)
            throw new Error('Insufficient balance')

        const extensions = await web3Enable('MOD')
        if (extensions.length === 0)
            throw new Error('SubWallet not found. Please install it.')
        
        const injector = await web3FromAddress(walletAddress)
        if (!injector?.signer)
            throw new Error('No signer available from SubWallet')

        const tx = api.tx.balances.transferKeepAlive(
            recipientAddress,
            transferAmount
        )

        const result = await new Promise<any>((resolve, reject) => {
            let unsub: (() => void) | undefined
            let resolved = false
            
            const timeout = setTimeout(() => {
            if (!resolved) {
                resolved = true
                if (unsub) unsub()
                reject(new Error('Transaction timeout after 120s'))
            }
            }, 120_000)

            tx.signAndSend(
            walletAddress,
            { signer: injector.signer },
            ({ status, dispatchError, txHash, events }) => {
                console.log('📊 Status:', status.type)

                if (dispatchError) {
                if (!resolved) {
                    resolved = true
                    clearTimeout(timeout)
                    if (unsub) unsub()
                    
                    if (dispatchError.isModule) {
                    const decoded = api.registry.findMetaError(dispatchError.asModule)
                    const { section, name, docs } = decoded
                    reject(new Error(`${section}.${name}: ${docs.join(' ')}`))
                    } else {
                    reject(new Error(dispatchError.toString()))
                    }
                }
                return
                }

                if (status.isInBlock) {
                console.log('✅ In block:', status.asInBlock.toHex())
                }
                
                if (status.isFinalized) {
                if (!resolved) {
                    resolved = true
                    clearTimeout(timeout)
                    console.log('🎉 Finalized:', status.asFinalized.toHex())
                    resolve({
                    success: true,
                    blockHash: status.asFinalized.toHex(),
                    txHash: txHash.toHex(),
                    events: events?.map(e => e.toHuman()),
                    })
                    if (unsub) unsub()
                }
                }
            }
            ).then((unsubFn) => {
            unsub = unsubFn
            }).catch((err) => {
            if (!resolved) {
                resolved = true
                clearTimeout(timeout)
                reject(err)
            }
            })
        })

        await api.disconnect()
        } catch (err: any) {
        console.error('❌ Transfer failed:', err)
        
        let msg = err?.message || String(err)

        if (msg.includes('1010')) 
            msg = 'Insufficient balance for fees.'
        else if (msg.toLowerCase().includes('cancel'))
            msg = 'Transaction cancelled by user.'
        else if (msg.includes('timeout'))
            msg = 'Transaction timeout. Please try again.'

            throw new Error(msg)
        } finally {
            // Ensure disconnection
            this.disconnect()
        }
    }

}


