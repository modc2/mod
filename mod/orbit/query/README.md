# Query Module

Unified interface for querying free AI models from OpenRouter and Venice AI.

## Features

- Automatic selection of free models from OpenRouter
- Support for Venice AI models
- Streaming and non-streaming responses
- Simple Python and CLI interfaces
- No API key management required (uses existing OpenRouter/Venice keys)

## Installation

The module is part of the mod framework. No additional installation needed.

## Usage

### Python API

```python
import mod as m

# Initialize the query module
q = m.mod('query')()

# Simple query (automatically uses free OpenRouter model)
response = q.query("What is the capital of France?")
print(response)  # "The capital of France is Paris."

# Streaming response
for chunk in q.query("Tell me a story", stream=True):
    print(chunk, end='')

# Use Venice AI instead
response = q.query("Explain quantum computing", use_venice=True)

# Specify a specific model
response = q.query("What is AI?", model="google/gemma-4-31b-it:free")

# List available free models
models = q.list_free_models(info=True)
for model in models:
    print(f"{model['id']} - {model['context_length']} tokens")
```

### CLI

Using the standalone script:

```bash
# Simple query
python query_free.py "What is the capital of France?"

# List available free models
python query_free.py --list

# Use Venice AI
python query_free.py "Explain AI" --venice

# Disable streaming
python query_free.py "Hello" --no-stream

# Specify model
python query_free.py "What is AI?" --model "google/gemma-4-31b-it:free"

# Custom max tokens
python query_free.py "Tell me a story" --max-tokens 2048
```

Using the mod CLI:

```bash
# Query via mod command
m query/query "What is the capital of France?"

# List free models
m query/list_free_models

# Query with Venice
m query/query "Explain AI" use_venice=true
```

## API Reference

### `query(query, use_venice=False, model=None, stream=False, max_tokens=4096, temperature=1.0)`

Query a free AI model with automatic provider selection.

**Parameters:**
- `query` (str): The question or prompt
- `use_venice` (bool): If True, use Venice instead of OpenRouter (default: False)
- `model` (str): Optional specific model to use
- `stream` (bool): Whether to stream the response (default: False)
- `max_tokens` (int): Maximum response tokens (default: 4096)
- `temperature` (float): Sampling temperature (default: 1.0)

**Returns:** str or generator

### `list_free_models(update=False, info=False)`

List all available free models from OpenRouter.

**Parameters:**
- `update` (bool): Force refresh from API
- `info` (bool): Return full model info instead of just IDs

**Returns:** list of model IDs or model info dicts

### `openrouter_query(query, model=None, stream=False, max_tokens=4096, temperature=1.0)`

Query using OpenRouter free models directly.

### `venice_query(query, model=None, stream=False, max_tokens=4096, temperature=1.0)`

Query using Venice AI directly.

## Available Free Models (as of April 2026)

OpenRouter currently offers 28+ free models including:

- **inclusionai/ling-2.6-flash:free** - 262K context
- **google/gemma-4-26b-a4b-it:free** - 262K context
- **google/gemma-4-31b-it:free** - 262K context
- **google/lyria-3-pro-preview** - 1M context
- **nvidia/nemotron-3-super-120b-a12b:free** - 262K context
- **minimax/minimax-m2.5:free** - 196K context
- **liquid/lfm-2.5-1.2b-thinking:free** - 32K context
- And many more...

Use `list_free_models()` to get the latest list.

## Examples

### Basic Question Answering

```python
q = m.mod('query')()
answer = q.query("What is machine learning?")
print(answer)
```

### Streaming Chat

```python
q = m.mod('query')()
print("AI: ", end='')
for chunk in q.query("Tell me a joke", stream=True):
    print(chunk, end='', flush=True)
print()
```

### Using Specific Models

```python
q = m.mod('query')()

# Use a specific free model
response = q.query(
    "Explain neural networks",
    model="google/gemma-4-31b-it:free",
    max_tokens=2048
)
```

### Venice AI Queries

```python
q = m.mod('query')()

# Query Venice AI
response = q.query(
    "What are uncensored LLMs?",
    use_venice=True
)
```

## Configuration

The module uses existing OpenRouter and Venice API keys. Make sure you have set up keys for the respective services:

```bash
# OpenRouter
m openrouter/add_key "sk-or-..."

# Venice
m venice/add_key "sk-venice-..."
```

## Dependencies

- mod framework
- openrouter module
- venice module
- openai library (for API client)

## Notes

- Free models are subject to rate limits and availability
- Not all free models support all features (e.g., vision, function calling)
- Response quality varies by model
- Some models may have usage restrictions or quotas

## Troubleshooting

**No free models available:**
```python
# Force refresh the model list
models = q.list_free_models(update=True)
```

**API key errors:**
```python
# Check if keys are configured
import mod as m
router = m.mod('openrouter')()
print(router.keys())
```

**Context length errors:**
```python
# Reduce max_tokens
response = q.query("...", max_tokens=2048)
```

## License

Part of the mod framework.
