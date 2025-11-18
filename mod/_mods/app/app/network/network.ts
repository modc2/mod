import { ApiPromise, WsProvider } from '@polkadot/api'
import { web3Enable, web3FromAddress } from '@polkadot/extension-dapp'

const networks = [
    { id: 'test', label: 'Modchain Devnet', url: 'wss://dev.api.modchain.ai' },
  ]
const network = 'test'
const UNIT = 1e12
const FEE_BUFFER = BigInt(100_000_000)

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

    async balance(address: string) : Promise<number> {
        await this.connect();
        if (!this.api) {
            throw new Error('API is not connected.')
        }
        const accountInfo: any = await this.api.query.system.account(address)
        const freeBalance = accountInfo.data.free.toBigInt()
        const formattedBalance = Number(freeBalance) / 1e12
        await this.disconnect();
        return formattedBalance;
    }

    async transfer(walletAddress: string, toAddress: string, amount: number) : Promise<any> {
      await this.connect()
      if (!this.api) throw new Error('API not connected')
      const api = this.api
      if (!api) throw new Error('API not connected')
      const recipientAddress = api.registry.createType(
        'AccountId32',
        toAddress
      ).toString()
      const amountFloat = parseFloat(amount.toString())
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

    const tx = api.tx.balances.transferKeepAlive(recipientAddress,transferAmount)
    const result = await this.submitTx(tx, walletAddress, injector)
    await this.disconnect()
    return result;
}

    async register(walletAddress: string, name: string, data: string, url: string, take: number) : Promise<any> {
      await this.connect()
      if (!this.api) throw new Error('API not connected')
      const api = this.api

      const extensions = await web3Enable('MOD')
      if (extensions.length === 0)
        throw new Error('SubWallet not found. Please install it.')
      
      const injector = await web3FromAddress(walletAddress)
      if (!injector?.signer)
        throw new Error('No signer available from SubWallet')

      const tx = api.tx.modules.registerModule(name, data, url, take)
      const result = await this.submitTx(tx, walletAddress, injector)
      await this.disconnect()
      return result;
    }

    async update(walletAddress: string, name: string, data: string, url: string, take: number) : Promise<any> {
      await this.connect()
      if (!this.api) throw new Error('API not connected')
      const api = this.api

      const extensions = await web3Enable('MOD')
      if (extensions.length === 0)
        throw new Error('SubWallet not found. Please install it.')
      
      const injector = await web3FromAddress(walletAddress)
      if (!injector?.signer)
        throw new Error('No signer available from SubWallet')

      const tx = api.tx.modules.updateModule(name, data, url, take)
      const result = await this.submitTx(tx, walletAddress, injector)
      await this.disconnect()
      return result;
    }

    async submitTx(tx: any, walletAddress: string, injector: any) : Promise<any> {

        if (!this.api) throw new Error('API not connected')
        const api = this.api;
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
                (result: any) => {
                  if (result.status.isInBlock || result.status.isFinalized) {
                    if (!resolved) {
                      resolved = true
                      clearTimeout(timeout)
                      if (unsub) unsub()
                      resolve({
                        blockHash: result.status.asInBlock?.toString() || result.status.asFinalized?.toString(),
                        status: result.status.isInBlock ? 'InBlock' : 'Finalized'
                      })
                    }
                  }
                }
              ).then((unsubFn: () => void) => {
                unsub = unsubFn
              }).catch((err: any) => {
                if (!resolved) {
                  resolved = true
                  clearTimeout(timeout)
                  reject(err)
                }
              })
            })
        return await result;
    }

}


