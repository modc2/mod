# Subspace Explorer TUI

A terminal user interface (TUI) for interacting with the Subspace blockchain, built with Rust and Ratatui.

## Features

- 🚀 Connect to any Substrate-based blockchain
- 📊 View real-time blockchain data
- 🔍 Explore blocks and transactions
- 💰 Manage accounts and balances
- 🎨 Customizable UI themes

## Installation

### Prerequisites

- Rust (latest stable version)
- Cargo (Rust's package manager)

### Building from Source

```bash
# Clone the repository
git clone https://github.com/yourusername/subspace-explorer.git
cd subspace-explorer

# Build in release mode
cargo build --release

# Run the application
./target/release/subspace-explorer
```

## Usage

```bash
# Start the application
subspace-explorer

# Connect to a custom node
subspace-explorer --node-url ws://localhost:9944

# Enable debug logging
RUST_LOG=debug subspace-explorer
```

## Keybindings

- `q`: Quit the application
- `h`, `j`, `k`, `l`: Navigation
- `?`: Show help

## Development

### Running Tests

```bash
cargo test
```

### Code Formatting

```bash
cargo fmt
```

### Linting

```bash
cargo clippy -- -D warnings
```

## License

This project is licensed under either of:

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or http://www.apache.org/licenses/LICENSE-2.0)
- MIT license ([LICENSE-MIT](LICENSE-MIT) or http://opensource.org/licenses/MIT)

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
