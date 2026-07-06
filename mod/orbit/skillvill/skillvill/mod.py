import os
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime


class Mod:
    description = """
    Skill registry and management system.
    Register, discover, and organize skills in local filesystem with module tree structure.
    """

    def __init__(self, base_path: Optional[str] = None):
        """Initialize skillvill with optional custom base path."""
        self.base_path = Path(base_path or os.path.expanduser("~/.mod/skills"))
        self.index_file = self.base_path / "index.json"
        self.module_tree = self.base_path / "modules"
        self._ensure_structure()

    def _ensure_structure(self):
        """Ensure base directory structure exists."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.module_tree.mkdir(parents=True, exist_ok=True)
        if not self.index_file.exists():
            self._save_index({})

    def _load_index(self) -> Dict[str, Any]:
        """Load the skill index."""
        if not self.index_file.exists():
            return {}
        with open(self.index_file, 'r') as f:
            return json.load(f)

    def _save_index(self, index: Dict[str, Any]):
        """Save the skill index."""
        with open(self.index_file, 'w') as f:
            json.dump(index, f, indent=2)

    def forward(self, skill_name: Optional[str] = None, **kwargs) -> Any:
        """
        Default entry point. List all skills or get specific skill.

        Args:
            skill_name: Optional skill name to retrieve
            **kwargs: Additional arguments

        Returns:
            All skills if no name provided, specific skill otherwise
        """
        if skill_name:
            return self.get(skill_name)
        return self.list(**kwargs)

    def register(
        self,
        name: str,
        skill_data: Dict[str, Any],
        module_path: Optional[str] = None,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Register a new skill in the local filesystem.

        Args:
            name: Skill name (e.g., 'agent/bash', 'uniswap/pools')
            skill_data: Skill metadata and configuration
            module_path: Optional path to module tree location
            overwrite: Whether to overwrite existing skill

        Returns:
            Registration result with path and metadata
        """
        index = self._load_index()

        if name in index and not overwrite:
            raise ValueError(f"Skill '{name}' already exists. Use overwrite=True to replace.")

        # Parse skill name for module tree structure
        parts = name.split('/')
        if len(parts) > 1:
            module_name = parts[0]
            skill_name = '/'.join(parts[1:])
        else:
            module_name = 'default'
            skill_name = name

        # Create module directory structure
        module_dir = self.module_tree / module_name
        module_dir.mkdir(parents=True, exist_ok=True)

        # Determine skill file path
        if module_path:
            skill_path = Path(module_path)
        else:
            skill_file = f"{skill_name.replace('/', '_')}.json"
            skill_path = module_dir / skill_file

        # Add metadata
        skill_data['_meta'] = {
            'name': name,
            'module': module_name,
            'skill': skill_name,
            'registered_at': datetime.utcnow().isoformat(),
            'path': str(skill_path)
        }

        # Save skill data
        with open(skill_path, 'w') as f:
            json.dump(skill_data, f, indent=2)

        # Update index
        index[name] = {
            'module': module_name,
            'skill': skill_name,
            'path': str(skill_path),
            'registered_at': skill_data['_meta']['registered_at']
        }
        self._save_index(index)

        return {
            'status': 'registered',
            'name': name,
            'path': str(skill_path),
            'module': module_name,
            'skill': skill_name
        }

    def unregister(self, name: str, delete_file: bool = False) -> Dict[str, str]:
        """
        Unregister a skill from the index.

        Args:
            name: Skill name to unregister
            delete_file: Whether to delete the skill file

        Returns:
            Unregistration result
        """
        index = self._load_index()

        if name not in index:
            raise ValueError(f"Skill '{name}' not found in registry")

        skill_info = index[name]

        if delete_file and os.path.exists(skill_info['path']):
            os.remove(skill_info['path'])

        del index[name]
        self._save_index(index)

        return {
            'status': 'unregistered',
            'name': name,
            'deleted_file': delete_file
        }

    def list(self, module: Optional[str] = None, pattern: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all registered skills, optionally filtered.

        Args:
            module: Filter by module name
            pattern: Search pattern for skill names

        Returns:
            List of skills with metadata
        """
        index = self._load_index()
        skills = []

        for name, info in index.items():
            if module and info['module'] != module:
                continue
            if pattern and pattern.lower() not in name.lower():
                continue

            skills.append({
                'name': name,
                **info
            })

        return sorted(skills, key=lambda x: x['name'])

    def get(self, name: str) -> Dict[str, Any]:
        """
        Get a specific skill by name.

        Args:
            name: Skill name

        Returns:
            Full skill data including content
        """
        index = self._load_index()

        if name not in index:
            raise ValueError(f"Skill '{name}' not found in registry")

        skill_info = index[name]
        skill_path = Path(skill_info['path'])

        if not skill_path.exists():
            raise FileNotFoundError(f"Skill file not found: {skill_path}")

        with open(skill_path, 'r') as f:
            skill_data = json.load(f)

        return skill_data

    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Search skills by name or metadata.

        Args:
            query: Search query string

        Returns:
            List of matching skills
        """
        return self.list(pattern=query)

    def tree(self, module: Optional[str] = None) -> Dict[str, Any]:
        """
        Get module tree structure of registered skills.

        Args:
            module: Optional module to show tree for

        Returns:
            Tree structure of skills organized by module
        """
        index = self._load_index()
        tree_structure = {}

        for name, info in index.items():
            if module and info['module'] != module:
                continue

            mod_name = info['module']
            if mod_name not in tree_structure:
                tree_structure[mod_name] = {
                    'skills': [],
                    'path': str(self.module_tree / mod_name)
                }

            tree_structure[mod_name]['skills'].append({
                'name': name,
                'skill': info['skill'],
                'path': info['path']
            })

        return tree_structure

    def import_skill(self, source_path: str, name: Optional[str] = None) -> Dict[str, Any]:
        """
        Import a skill from an external path.

        Args:
            source_path: Path to skill JSON file
            name: Optional custom name for the skill

        Returns:
            Import result
        """
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        with open(source, 'r') as f:
            skill_data = json.load(f)

        # Use provided name or extract from skill data
        skill_name = name or skill_data.get('_meta', {}).get('name') or source.stem

        return self.register(skill_name, skill_data)

    def export_skill(self, name: str, dest_path: str) -> Dict[str, str]:
        """
        Export a skill to an external path.

        Args:
            name: Skill name to export
            dest_path: Destination path for export

        Returns:
            Export result
        """
        skill_data = self.get(name)
        dest = Path(dest_path)

        dest.parent.mkdir(parents=True, exist_ok=True)

        with open(dest, 'w') as f:
            json.dump(skill_data, f, indent=2)

        return {
            'status': 'exported',
            'name': name,
            'path': str(dest)
        }

    def validate(self, name: str) -> Dict[str, Any]:
        """
        Validate a skill's data and file integrity.

        Args:
            name: Skill name to validate

        Returns:
            Validation result
        """
        index = self._load_index()

        if name not in index:
            return {
                'valid': False,
                'error': 'Skill not found in index'
            }

        skill_info = index[name]
        skill_path = Path(skill_info['path'])

        if not skill_path.exists():
            return {
                'valid': False,
                'error': f"Skill file missing: {skill_path}"
            }

        try:
            with open(skill_path, 'r') as f:
                skill_data = json.load(f)

            # Validate required fields
            if '_meta' not in skill_data:
                return {
                    'valid': False,
                    'error': 'Missing _meta field'
                }

            return {
                'valid': True,
                'name': name,
                'path': str(skill_path),
                'size': skill_path.stat().st_size
            }
        except json.JSONDecodeError as e:
            return {
                'valid': False,
                'error': f'Invalid JSON: {str(e)}'
            }

    def health(self) -> Dict[str, Any]:
        """
        Check health of skillvill registry.

        Returns:
            Health status
        """
        index = self._load_index()
        total_skills = len(index)
        valid_skills = 0
        invalid_skills = []

        for name in index.keys():
            result = self.validate(name)
            if result['valid']:
                valid_skills += 1
            else:
                invalid_skills.append({
                    'name': name,
                    'error': result.get('error')
                })

        return {
            'status': 'healthy' if len(invalid_skills) == 0 else 'degraded',
            'total_skills': total_skills,
            'valid_skills': valid_skills,
            'invalid_skills': invalid_skills,
            'base_path': str(self.base_path),
            'module_tree': str(self.module_tree)
        }

    def status(self) -> Dict[str, Any]:
        """Get current status of skillvill."""
        return self.health()

    def serve(self, port: int = 50140):
        """Start skillvill API server (placeholder)."""
        raise NotImplementedError("API server not yet implemented")

    def kill(self):
        """Stop skillvill API server (placeholder)."""
        raise NotImplementedError("API server not yet implemented")
