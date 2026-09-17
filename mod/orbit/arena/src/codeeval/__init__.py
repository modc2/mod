"""codeeval — coding reconstruction benchmark tools for the arena.

Usage:
    from arena.src.codeeval.harvest import harvest, generate_game_source

    tasks = harvest(Path('/path/to/orbit'))
    src = generate_game_source(tasks)
    with open('coderecon.py', 'w') as f:
        f.write(src)
"""
