import os
import glob
import mod as m

class Mod:
    description = """Skill registry — list and read skill.md files for orbit modules."""
    path = r'/root/mod/mod/orbit/skill'

    def forward(self, **kwargs):
        """Default entry point."""
        query = kwargs.get('query')
        if query:
            return self.search(query)
        name = kwargs.get('name')
        if name:
            return self.get_skill(name)
        return self.list_skills()

    def list_skills(self):
        """Return [{name, hint}] for all modules that have a skill.md file."""
        orbit_root = os.path.dirname(self.path)
        pattern = os.path.join(orbit_root, '*/skill.md')
        paths = sorted(glob.glob(pattern))
        return [{'name': os.path.basename(os.path.dirname(p)), 'hint': self._extract_hint(p)} for p in paths]

    def search(self, query):
        """Return [{name, hint}] for modules whose name, hint, or skill body matches query."""
        orbit_root = os.path.dirname(self.path)
        pattern = os.path.join(orbit_root, '*/skill.md')
        paths = sorted(glob.glob(pattern))
        q = query.lower()
        results = []
        for p in paths:
            name = os.path.basename(os.path.dirname(p))
            try:
                text = m.get_text(p)
            except Exception:
                text = ''
            hint = self._hint_from_text(text)
            if q in name.lower() or q in hint.lower() or q in text.lower():
                results.append({'name': name, 'hint': hint})
        return results

    def _extract_hint(self, skill_path):
        """Extract a one-line hint from a skill.md file."""
        try:
            text = m.get_text(skill_path)
        except Exception:
            return ''
        return self._hint_from_text(text)

    def _hint_from_text(self, text):
        """Extract a one-line hint from skill.md text."""
        # Check for YAML frontmatter description:
        if text.startswith('---'):
            end = text.find('\n---', 3)
            if end != -1:
                frontmatter = text[3:end]
                for line in frontmatter.splitlines():
                    if line.startswith('description:'):
                        return line[len('description:'):].strip()
        # Fallback: first non-empty, non-heading line
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                return stripped
        return ''

    def get_skill(self, name):
        """Return the skill.md content for the named orbit module."""
        orbit_root = os.path.dirname(self.path)
        skill_path = os.path.join(orbit_root, name, 'skill.md')
        if not os.path.exists(skill_path):
            raise ValueError(f"No skill.md found for module '{name}'")
        return m.get_text(skill_path)

    def info(self):
        """Return module info."""
        return {
            'name': 'skill',
            'description': self.description,
            'path': self.path,
            'files': os.listdir(self.path),
        }

    def readme(self):
        """Return the project README."""
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
