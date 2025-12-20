# Python Decorator (Wrapper Function) Example
# The @ sign is used to apply decorators

import time
from functools import wraps

# Example 1: Simple Timer Decorator
def timer_decorator(func):
    """Measures execution time of a function"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        print(f"{func.__name__} took {end_time - start_time:.4f} seconds")
        return result
    return wrapper

# Example 2: Logger Decorator
def logger_decorator(func):
    """Logs function calls with arguments"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        print(f"Calling {func.__name__} with args={args}, kwargs={kwargs}")
        result = func(*args, **kwargs)
        print(f"{func.__name__} returned {result}")
        return result
    return wrapper

# Example 3: Decorator with Arguments
def repeat(times):
    """Repeats function execution N times"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            results = []
            for _ in range(times):
                results.append(func(*args, **kwargs))
            return results
        return wrapper
    return decorator

# USING THE DECORATORS WITH @ SIGN

@timer_decorator
def slow_function():
    """A function that takes some time"""
    time.sleep(1)
    return "Done!"

@logger_decorator
def add_numbers(a, b):
    """Adds two numbers"""
    return a + b

@repeat(times=3)
def greet(name):
    """Greets a person"""
    return f"Hello, {name}!"

# STACKING MULTIPLE DECORATORS
@timer_decorator
@logger_decorator
def multiply(x, y):
    """Multiplies two numbers"""
    return x * y

# USAGE EXAMPLES
if __name__ == "__main__":
    print("=== Example 1: Timer Decorator ===")
    slow_function()
    
    print("\n=== Example 2: Logger Decorator ===")
    add_numbers(5, 3)
    
    print("\n=== Example 3: Repeat Decorator ===")
    results = greet("World")
    print(f"Results: {results}")
    
    print("\n=== Example 4: Stacked Decorators ===")
    multiply(4, 7)
