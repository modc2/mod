# Utilities Reference

`mod/core/utils.py` (75KB, 2700+ lines) provides battle-tested utility functions used throughout the framework. These are importable directly or accessible through the `m` object.

## Import & Module Loading

```python
from mod.core.utils import import_module, import_object

# Import a Python module
module = import_module('os.path')

# Import an object from a string path (supports / :: . separators)
fn = import_object('mod.core.mod/Mod')
fn = import_object('mypackage::MyClass')
fn = import_object('mypackage.mymodule.MyClass')
```

## Networking & Ports

```python
from mod.core.utils import (
    port_used, port_available, free_port, free_ports,
    kill_port, external_ip
)

# Check port status
port_used(8000)          # True if something is listening
port_available(8000)     # True if free

# Get available ports
free_port()              # Single free port
free_ports(3)            # [8001, 8002, 8003]

# Kill process on port
kill_port(8000)

# Get external IP (tries curl, ipify, AWS, dnsomatic, wikipedia)
external_ip()            # '203.0.113.42'
```

## File Operations

```python
from mod.core.utils import (
    get_text, put_text, get_json, put_json,
    get_yaml, put_yaml, file2text, cp, mv, rm
)

# Text files
text = get_text('/path/to/file.txt', default='')
put_text('/path/to/file.txt', 'content')

# JSON files
data = get_json('/path/to/config.json', default={})
put_json('/path/to/config.json', {'key': 'value'})

# YAML files
data = get_yaml('/path/to/config.yaml', default={})
put_yaml('/path/to/config.yaml', {'key': 'value'})

# Read all files in a directory to dict
contents = file2text('/path/to/dir')
# {'file1.py': 'content...', 'file2.py': 'content...'}

# File operations
cp('/src/path', '/dst/path')          # Copy
mv('/old/path', '/new/path')          # Move
rm('/path/to/delete')                 # Remove
```

## Data Conversion

```python
from mod.core.utils import (
    python2str, str2python, bytes2str, str2bytes, hash
)

# Python ↔ String
python2str({'key': 'value'})    # '{"key": "value"}'
str2python('{"key": "value"}')  # {'key': 'value'}
str2python('None')              # None
str2python('true')              # True
str2python('[1,2,3]')           # [1, 2, 3]
str2python('42')                # 42

# Bytes ↔ String
bytes2str(b'\x00\x01', mode='hex')  # '0001'
str2bytes('0001', mode='hex')       # b'\x00\x01'

# Hashing (sha256, md5, keccak, ss58, etc.)
hash('hello', mode='sha256')
hash('hello', mode='md5')
hash('hello', mode='keccak')
```

## System Information

```python
from mod.core.utils import hardware, cpu_info, memory_info, gpu_info, disk_info

# Full system info
info = hardware()
# {'cpu': {...}, 'memory': {...}, 'disk': {...}, 'gpu': {...}}

# Individual queries
cpu_info()                    # {'count': 8, 'type': 'arm64'}
memory_info(fmt='gb')         # {'total': 16.0, 'used': 8.5, ...}
gpu_info(fmt='gb')            # {'total': 8.0, ...}
disk_info('/', fmt='gb')      # {'total': 500.0, 'used': 250.0, ...}
```

## Async & Threading

```python
from mod.core.utils import thread, wait, gather, as_completed, executor

# Create a thread
t = thread(fn=my_function, args=(1, 2), daemon=True, start=True)

# Wait for futures
results = wait(futures, timeout=30)

# Gather async jobs
results = gather(async_jobs, timeout=30)

# Iterate as completed
for result in as_completed(futures, timeout=30):
    print(result)

# Get an executor
pool = executor(max_workers=4, mode='thread')   # ThreadPoolExecutor
pool = executor(max_workers=4, mode='process')  # ProcessPoolExecutor
```

## Command Execution

```python
from mod.core.utils import cmd

# Run a shell command
output = cmd('ls -la')

# With options
output = cmd(
    'npm install',
    verbose=True,       # Print output in real-time
    cwd='/path/to/dir', # Working directory
    env={'NODE_ENV': 'production'},
    sudo=True,          # Run with sudo
    timeout=60,         # Timeout in seconds
    stream=True,        # Stream output
)

# Get process object
process = cmd('long_running_task', return_process=True)
```

## Data Structures

```python
from mod.core.utils import chunk, shuffle, choice, sample, mean, median, stdev

# Chunk a list
chunk([1,2,3,4,5], chunk_size=2)    # [[1,2], [3,4], [5]]
chunk([1,2,3,4,5], num_chunks=2)    # [[1,2,3], [4,5]]

# Random operations
shuffle([1,2,3,4,5])        # [3,1,5,2,4] (in-place)
choice([1,2,3])             # 2 (random)
choice({'a': 1, 'b': 2})   # ('b', 2) (random key-value)
sample([1,2,3,4,5], n=3)   # [2,5,1]

# Statistics
mean([1,2,3,4,5])     # 3.0
median([1,2,3,4,5])   # 3
stdev([1,2,3,4,5])    # 1.58...
```

## Logging & Display

```python
from mod.core.utils import print_console, status, error, success, warning

# Rich console output
print_console('Hello', color='green')
print_console('Warning!', color='yellow')

# Status messages
success('Operation completed')
error('Something went wrong')
warning('Be careful')

# Rich status spinner (context manager)
with status('Processing...'):
    do_something()
```

## Error Handling

```python
from mod.core.utils import detailed_error, retry

# Get detailed error info
try:
    risky_operation()
except Exception as e:
    info = detailed_error(e)
    # {'file': 'mod.py', 'line': 42, 'error': 'ValueError: ...', 'traceback': '...'}

# Retry decorator
@retry(trials=3, verbose=True)
def flaky_api_call():
    return requests.get('https://api.example.com')
```

## Timing

```python
from mod.core.utils import timer, spinner

# Timer context manager
with timer('my_operation'):
    do_something()
# → "my_operation: 1.23s"

# Spinner context manager
with spinner('Loading...'):
    do_something()
# → Shows animated spinner until done
```

## Package Management

```python
from mod.core.utils import ensure_lib, pip_install

# Ensure a package is installed (installs if missing)
ensure_lib('requests')
ensure_lib('pandas', verbose=True)

# Direct pip install
pip_install('flask', upgrade=True)
```
