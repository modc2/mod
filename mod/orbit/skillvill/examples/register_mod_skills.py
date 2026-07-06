#!/usr/bin/env python3
"""
Example: Register all mod orbit module functions as skills in skillvill.

This demonstrates how skillvill can be used to maintain a registry of
all available mod functions across the entire ecosystem.
"""

import mod as m


def register_all_mod_skills():
    """Register all mod orbit module functions as skills."""
    sv = m.mod('skillvill')()

    print("Registering mod orbit module skills...\n")

    # Get all modules
    all_mods = m.mods()
    print(f"Found {len(all_mods)} modules")

    registered_count = 0
    skipped_count = 0

    for mod_name in all_mods:
        try:
            # Get functions for this module
            fns = m.fns(mod_name)

            if not fns:
                print(f"  {mod_name}: no functions")
                continue

            for fn in fns:
                skill_name = f"{mod_name}/{fn}"

                # Get schema if available
                try:
                    schema = m.schema(f"{mod_name}/{fn}")
                except:
                    schema = None

                # Create skill data
                skill_data = {
                    'name': fn,
                    'module': mod_name,
                    'type': 'mod_function',
                    'description': f'{mod_name} module function: {fn}',
                    'schema': schema
                }

                try:
                    # Register skill
                    sv.register(skill_name, skill_data, overwrite=True)
                    registered_count += 1
                    print(f"  ✓ {skill_name}")
                except Exception as e:
                    skipped_count += 1
                    print(f"  ✗ {skill_name}: {e}")

        except Exception as e:
            print(f"  ✗ {mod_name}: {e}")

    print(f"\nRegistration complete!")
    print(f"  Registered: {registered_count}")
    print(f"  Skipped: {skipped_count}")

    # Show tree
    print("\n--- Module Tree ---")
    tree = sv.tree()
    for module, data in sorted(tree.items()):
        print(f"  {module}: {len(data['skills'])} skills")

    # Health check
    health = sv.health()
    print(f"\nHealth: {health['status']}")
    print(f"Total skills: {health['total_skills']}")


if __name__ == '__main__':
    register_all_mod_skills()
