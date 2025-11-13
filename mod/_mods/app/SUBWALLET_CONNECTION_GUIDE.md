# SubWallet Connection Guide

## Where to Find the SubWallet Connection Option

The SubWallet connection option is located in the **authentication modal** that appears when you click the **SIGN IN** button.

### Step-by-Step Instructions:

1. **Locate the SIGN IN Button**
   - Look at the top-right corner of the application header
   - You'll see a green "SIGN IN" button

2. **Open the Authentication Modal**
   - Click the "SIGN IN" button
   - A modal dialog will appear with the title "AUTHENTICATE"

3. **Select SubWallet Mode**
   - In the modal, you'll see two tabs at the top:
     - **LOCAL KEY** - For password-based authentication
     - **SUBWALLET** - For wallet extension authentication
   - Click on the **SUBWALLET** tab

4. **Connect Your Wallet**
   - If you have SubWallet or Polkadot.js extension installed, you'll see a dropdown menu
   - Select your account from the dropdown
   - Click "SIGN IN" to connect

### Requirements:

- You must have either **SubWallet** or **Polkadot.js** browser extension installed
- The extension must have at least one account created
- The extension must be unlocked and accessible

### Technical Details:

**File Location:** `/root/mod/mod/modules/app/app/block/header/WalletAuthButton.tsx`

**Component Hierarchy:**
- Header Component → AuthButton → WalletAuthButton
- The WalletAuthButton component handles both local and SubWallet authentication
- When SubWallet mode is selected, it uses `@polkadot/extension-dapp` to connect

### Features:

- **Derived Key System**: A local key is derived from your wallet address for client operations
- **No Repeated Signing**: You won't need to sign every request after initial connection
- **Wallet Metadata Storage**: Your wallet address and type are stored in localStorage
- **Visual Indicator**: Connected SubWallet accounts show a 🔗 icon next to the address

### Troubleshooting:

- If the SUBWALLET button is disabled, install a compatible wallet extension
- If no accounts appear, create an account in your wallet extension first
- If connection fails, ensure your wallet extension is unlocked

## Code Reference:

The authentication modal with SubWallet option is rendered in:
```typescript
// Line ~80-140 in WalletAuthButton.tsx
<div className="flex gap-2 mb-4">
  <button onClick={() => setAuthMode('local')}>LOCAL KEY</button>
  <button onClick={() => setAuthMode('subwallet')}>SUBWALLET</button>
</div>
```
