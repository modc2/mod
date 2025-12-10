# Commune CLI Module

## Overview

The Commune CLI is a pythonic command-line interface that provides a simple way to interact with the Commune library. Unlike traditional CLI tools that use argparse, Commune's CLI offers a more intuitive, Python-like experience for testing functions and modules.

## Basic Usage

The CLI follows two main patterns:

```bash
# Pattern 1: Default module is "mod"
m {function_name} *args **kwargs

# Pattern 2: Specify both module and function
m {module_name}/{function_name} *args **kwargs
```

### Examples

```bash
# List files in current directory using the default module
m ls ./

# Equivalent to the above but with explicit module
m module/ls ./

# Get code of a module
m module/code
```

## Module Naming Conventions

Commune uses simplified naming conventions:

- `mod/module.py` → `mod`
- `storage/module.py` → `storage`
- `storage/storage/module.py` → `storage`

The root module is the one closest to the mod/ repository.

## Common Operations

### Creating a New Module

```bash
# CLI command
m new_module agi

# Equivalent Python code
# import mod as m
# m.new_module("agi")
```

This creates a new module called `agi` in the `modules` directory.

### Getting Module Configuration

```bash
# CLI command
m agi/config

# Equivalent Python code
# import mod as m
# m.mod("agi").config()
```

If a module doesn't have a config or YAML file, keyword arguments will be used as the config.

### Getting Module Code

```bash
# CLI command
m agi/code

# Equivalent Python code
# import mod as m
# m.mod("agi").code()
```

### Serving a Module

```bash
m serve module
```

### Calling Module Functions

```bash
# Basic function call
m call module/ask hey
# Equivalent to: m.call('module/ask', 'hey')
# or: m.connect('mod').ask('hey')

# With additional parameters
m call module/ask hey stream=1
# Equivalent to: m.call('module/ask', 'hey', stream=1)
# or: m.connect('mod').ask('hey', stream=1)
```

## Shortcuts and Tips

- `c` (with no arguments) navigates to the Commune repository
- `m module/` calls the module's forward function
- `m module/forward` explicitly calls the forward function
- `m module/add a=1 b=1` is equivalent to `m module/add 1 1`
- `m ai what is the point of love` calls the AI module with a prompt

## Current Limitations

- Lists and dictionaries are not directly supported in CLI arguments
- Only positional arguments are supported
- Only one function can be called at a time

## Python Equivalent

All CLI commands have equivalent Python code using the Commune library:

```python
import mod as m

# Example: listing files
m.ls('./')

# Example: calling a module function
m.call('module/ask', 'hey', stream=1)
```
