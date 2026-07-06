#!/usr/bin/env python3
"""
Example: Import and export skills between registries.

This demonstrates how to move skills between different skillvill
instances or share skills with other systems.
"""

import mod as m
import os


def import_export_example():
    """Example of importing and exporting skills."""

    # Create two separate registries
    sv_source = m.mod('skillvill')(base_path='/tmp/skills_source')
    sv_dest = m.mod('skillvill')(base_path='/tmp/skills_dest')

    print("=== Source Registry ===\n")

    # Register skills in source
    source_skills = {
        'api/users': {
            'name': 'users',
            'type': 'endpoint',
            'method': 'GET',
            'path': '/api/users',
            'description': 'List all users'
        },
        'api/posts': {
            'name': 'posts',
            'type': 'endpoint',
            'method': 'GET',
            'path': '/api/posts',
            'description': 'List all posts'
        },
        'db/migrate': {
            'name': 'migrate',
            'type': 'database',
            'description': 'Run database migrations'
        }
    }

    for name, data in source_skills.items():
        sv_source.register(name, data)
        print(f"Registered: {name}")

    print(f"\nSource skills: {len(sv_source.list())}")

    print("\n=== Export Skills ===\n")

    # Export each skill
    export_dir = '/tmp/skill_exports'
    os.makedirs(export_dir, exist_ok=True)

    for skill_name in source_skills.keys():
        export_path = f"{export_dir}/{skill_name.replace('/', '_')}.json"
        result = sv_source.export_skill(skill_name, export_path)
        print(f"Exported {result['name']} -> {result['path']}")

    print("\n=== Import to Destination ===\n")

    # Import skills to destination registry
    for skill_name in source_skills.keys():
        export_path = f"{export_dir}/{skill_name.replace('/', '_')}.json"
        result = sv_dest.import_skill(export_path, name=skill_name)
        print(f"Imported: {result['name']}")

    print(f"\nDestination skills: {len(sv_dest.list())}")

    print("\n=== Compare Registries ===\n")

    source_tree = sv_source.tree()
    dest_tree = sv_dest.tree()

    print("Source modules:")
    for module in sorted(source_tree.keys()):
        print(f"  - {module}: {len(source_tree[module]['skills'])} skills")

    print("\nDestination modules:")
    for module in sorted(dest_tree.keys()):
        print(f"  - {module}: {len(dest_tree[module]['skills'])} skills")

    # Verify skills match
    source_skills_list = sorted([s['name'] for s in sv_source.list()])
    dest_skills_list = sorted([s['name'] for s in sv_dest.list()])

    if source_skills_list == dest_skills_list:
        print("\n✓ All skills successfully transferred!")
    else:
        print("\n✗ Mismatch detected")

    # Health check both
    print("\n=== Health Checks ===\n")
    source_health = sv_source.health()
    dest_health = sv_dest.health()

    print(f"Source: {source_health['status']} ({source_health['valid_skills']}/{source_health['total_skills']} valid)")
    print(f"Dest: {dest_health['status']} ({dest_health['valid_skills']}/{dest_health['total_skills']} valid)")


if __name__ == '__main__':
    import_export_example()
