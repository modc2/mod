# mod Server Module

The mod Server module provides a robust, extensible server framework for creating and managing API endpoints within the mod ecosystem. It includes authentication, middleware, process management, and client-server communication capabilities.

## Overview

This module is the backbone of mod's distributed architecture, allowing modules to expose their functionality as API endpoints. The server module handles:

- Authentication and authorization
- Request/response handling
- Process management
- Rate limiting
- Client-server communication

## Core Components

### Server

The main `Server` class provides the foundation for creating and managing API endpoints. It includes functionality for:

- Setting up FastAPI endpoints
- Managing module functions as API endpoints
- Handling authentication and authorization
- Managing server processes
- Rate limiting and request validation

### Auth

The `Auth` class provides authentication and authorization capabilities using JWT tokens. It includes:

- Token generation and verification
- Header authentication
- Signature verification
- Role-based access control

### Client

The `Client` class provides a simple interface for making requests to server endpoints. It includes:

- Request formatting and sending
- Response handling
- Connection management
- Stream handling

### ProcessManager

The `ProcessManager` class provides utilities for managing server processes using PM2. It includes:

- Starting and stopping processes (`serve`/`start` and `kill`/`stop` are aliases)
- Process monitoring
- Log management

### Middleware

The `Middleware` class provides request/response middleware for FastAPI. It includes:

- Rate limiting
- Request size validation
- Authentication verification

## Usage

### Creating a Server

`serve` and `start` are interchangeable, as are `kill` and `stop`.

```python
import mod as m

# Create and serve a module (serve and start are aliases)
m.serve('my_module', port=8000)
m.start('my_module', port=8000)  # same as serve

# Kill a server (kill and stop are aliases)
m.kill('my_module')
m.stop('my_module')  # same as kill
```

**CLI:**
```bash
m serve my_module port=8000
m start my_module port=8000   # same as serve

m kill my_module
m stop my_module              # same as kill
```

### Making Client Requests

```python
import mod as m

# Simple call
result = m.call('my_module/function', arg1=value1, arg2=value2)

# Using client
client = m.mod('client')(url='my_module')
result = client.forward(fn='function', kwargs={'arg1': value1, 'arg2': value2})

# Virtual client
module = m.connect('my_module')
result = module.function(arg1=value1, arg2=value2)
```

### Authentication

```python
import mod as m

# Create auth instance
auth = m.mod('auth')()

# Generate token
token = auth.get_token(data={'user_id': 123})

# Verify token
verified = auth.verify_token(token)
```

## Advanced Features

### Role-Based Access Control

The server module includes role-based access control for managing permissions:

```python
# Add a role to a user
add_role(address='user_address', role='admin')

# Check a user's role
role = get_role(address='user_address')

# Remove a role
remove_role(address='user_address', role='admin')
```

### Rate Limiting

The server includes rate limiting based on user roles and network state:

```python
# Get rate limit for a user and function
rate_limit = rate_limit(user='user_address', fn='function_name')

# Get current rate for a user
rate = rate(user='user_address')
```

### Process Management

Manage server processes using the ProcessManager:

```python
pm = m.mod('pm')()

# Start a process
pm.run(fn='module/function', name='process_name')

# Kill a process
pm.kill('process_name')

# View logs
logs = pm.logs('process_name')
```

## Contributing

Contributions to the server module are welcome. Please ensure that any changes maintain backward compatibility and include appropriate tests.
