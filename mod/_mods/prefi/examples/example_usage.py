#!/usr/bin/env python3
"""Example usage of the Prefi class."""

from prefi import Prefi


def main():
    # Create a Prefi instance with 'Mr_' prefix
    prefi = Prefi('Mr_')
    
    # Add prefix to words
    print("Adding prefixes:")
    print(prefi.add_prefix('Robot'))  # Output: Mr_Robot
    print(prefi.add_prefix('Anderson'))  # Output: Mr_Anderson
    
    # Remove prefix from words
    print("\nRemoving prefixes:")
    print(prefi.remove_prefix('Mr_Robot'))  # Output: Robot
    print(prefi.remove_prefix('Anderson'))  # Output: Anderson (no prefix to remove)
    
    # Different prefix example
    print("\nUsing different prefix:")
    api_prefi = Prefi('api_')
    print(api_prefi.add_prefix('endpoint'))  # Output: api_endpoint
    print(api_prefi.add_prefix('handler'))  # Output: api_handler


if __name__ == '__main__':
    main()
