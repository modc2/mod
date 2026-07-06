#!/usr/bin/env python3
"""
Example: Using skillvill with custom storage path.

This demonstrates how to use skillvill with a custom base path
for project-specific skill registries.
"""

import mod as m


def custom_storage_example():
    """Example of using custom storage path."""

    # Initialize with custom path
    sv = m.mod('skillvill')(base_path='/tmp/my_project_skills')

    print("Using custom storage path: /tmp/my_project_skills\n")

    # Register project-specific skills
    project_skills = {
        'build/compile': {
            'name': 'compile',
            'type': 'build_step',
            'description': 'Compile TypeScript code',
            'command': 'tsc',
            'inputs': ['src/**/*.ts'],
            'outputs': ['dist/**/*.js']
        },
        'build/test': {
            'name': 'test',
            'type': 'build_step',
            'description': 'Run test suite',
            'command': 'jest',
            'inputs': ['src/**/*.test.ts'],
            'outputs': ['coverage/']
        },
        'deploy/staging': {
            'name': 'staging',
            'type': 'deployment',
            'description': 'Deploy to staging environment',
            'target': 'staging.example.com',
            'pre_deploy': ['build/compile', 'build/test']
        },
        'deploy/production': {
            'name': 'production',
            'type': 'deployment',
            'description': 'Deploy to production',
            'target': 'example.com',
            'pre_deploy': ['build/compile', 'build/test'],
            'requires_approval': True
        }
    }

    # Register all skills
    for skill_name, skill_data in project_skills.items():
        result = sv.register(skill_name, skill_data)
        print(f"Registered: {result['name']}")

    print("\n--- Project Skills Tree ---")
    tree = sv.tree()
    for module, data in tree.items():
        print(f"\n{module}:")
        for skill in data['skills']:
            print(f"  - {skill['skill']}")

    # Search for deployment skills
    print("\n--- Deployment Skills ---")
    deploy_skills = sv.list(module='deploy')
    for skill in deploy_skills:
        skill_data = sv.get(skill['name'])
        print(f"{skill['name']}: {skill_data['description']}")

    # Health check
    health = sv.health()
    print(f"\nHealth: {health['status']}")
    print(f"Base path: {health['base_path']}")
    print(f"Total skills: {health['total_skills']}")


if __name__ == '__main__':
    custom_storage_example()
