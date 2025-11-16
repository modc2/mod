# User Profile Module

This mod provides a user profile panel that slides in from the right side of the screen with enhanced security features.

## Features

- **Wallet Information Display**: Shows user address, crypto type, and balance
- **Message Signing**: Allows users to sign messages using their private key
- **AUTO-VERIFICATION**: Every signature is automatically verified immediately after signing to prevent tampering
- **Signature Verification**: Verify any signature with public keys to ensure authenticity
- **Security Focused UI**: Clear explanations help users understand what they're signing and verifying
- **Quick Actions**: Convenient shortcuts for common operations

## Components

### UserProfile

The main component that renders the user profile panel with enhanced security features.

#### Props

- `user`: User object containing address, crypto_type, and balance
- `isOpen`: Boolean to control panel visibility
- `onClose`: Callback function when panel is closed
- `keyInstance`: Key instance for cryptographic operations (must support sign() and verify() methods)

## Usage

The UserProfile component is integrated into the Header component. When a user is logged in, they can click on their address in the top right to expand the profile panel.

### Sign a Message

1. Enter your message in the "MESSAGE TO SIGN" text area
2. Click the "$ SIGN" button
3. The signature will be displayed in the "SIGNATURE OUTPUT" field
4. **AUTOMATIC VERIFICATION**: The signature is immediately verified against your public key
5. You'll see a green success message if verification passes, or red error if something is wrong
6. Copy the signature using the 📋 COPY button

**Security Note**: The auto-verification feature ensures that no one can tamper with your signature between signing and using it. If the auto-verify fails, DO NOT use that signature.

### Verify a Signature

1. Enter the original message in "ORIGINAL MESSAGE"
2. Paste the signature in "SIGNATURE TO VERIFY"
3. Enter the public key (or click "USE MY KEY" to use your own)
4. Click "$ VERIFY" to check validity
5. Green ✓ means valid, Red ✗ means invalid or error

**Why Verify?**: Always verify signatures from others before trusting them. This prevents attacks where someone tries to impersonate another user or modify signed data.

## Security Features

### Auto-Verification on Sign
- Every time you sign a message, it's automatically verified
- Prevents sneaky attacks where malware might try to replace your signature
- Gives you immediate confidence that your signature is valid
- Uses the same verification logic as manual verification

### Clear User Education
- Explanatory text helps users understand what signing and verifying means
- Warning symbols (⚠️) and security icons (🔒, 🛡️) draw attention to important info
- Color-coded results (green = good, red = bad) for quick understanding
- Security info panel explains best practices

### Visual Feedback
- Sign results show success/failure immediately
- Auto-verify results are prominently displayed with border and background
- Verify results are clearly color-coded
- All error messages are descriptive

## Styling

The component uses Tailwind CSS with a terminal/hacker aesthetic:
- Green text on black background (#00ff00 on #000000)
- Monospace font for that terminal feel
- Terminal-style command prompts ($ prefix)
- Smooth slide-in animation from the right
- Color-coded status messages (green for success, red for errors)
- Border highlights for important sections

## Technical Details

### Key Instance Requirements

The `keyInstance` prop must provide:
- `sign(message: string): string` - Signs a message and returns signature
- `verify(message: string, signature: string, publicKey: string): boolean` - Verifies a signature
- `publicKey` or `public_key` property - The user's public key

### State Management

The component manages several pieces of state:
- `message`, `signature` - For signing operations
- `verifyMessage`, `verifySignature`, `verifyPublicKey` - For verification
- `signResult`, `verifyResult`, `autoVerifyResult` - Status messages

### Auto-Verification Logic

Implemented via `useEffect` hook that triggers whenever:
- A new signature is created
- The message changes
- The keyInstance changes

This ensures real-time verification without user action.

## Best Practices

1. **Always check the auto-verify result** after signing
2. **Never use a signature** that failed auto-verification
3. **Always verify signatures** from others before trusting them
4. **Keep your private key secure** - it never leaves your device
5. **Understand what you're signing** - read the message carefully

## Future Enhancements

- Support for multiple signature algorithms
- Signature history/log
- Batch signing/verification
- Export/import signature sets
- Integration with hardware wallets