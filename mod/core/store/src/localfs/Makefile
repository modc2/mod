.PHONY: install build test clean help

help:
	@echo "LocalFS Makefile"
	@echo ""
	@echo "Available targets:"
	@echo "  install      - Install Python dependencies"
	@echo "  build        - Build Rust bindings (requires Rust)"
	@echo "  build-python - Install in development mode (Python only)"
	@echo "  test         - Run test suite"
	@echo "  clean        - Remove build artifacts"
	@echo "  help         - Show this help message"

install:
	pip install -r requirements.txt

build: install
	@if command -v cargo >/dev/null 2>&1; then \
		echo "Building Rust bindings..."; \
		cd rust && maturin develop --release; \
	else \
		echo "Rust not found. Install with: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"; \
		exit 1; \
	fi

build-python: install
	pip install -e .

test:
	python test_localfs.py

clean:
	rm -rf build/ dist/ *.egg-info
	rm -rf rust/target
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type f -name '*.so' -delete

.DEFAULT_GOAL := help
