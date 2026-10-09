import os
import re
import subprocess
import mod as m

class Mod:
    description = """Run pytest on a module path and return structured test results"""
    path = r'/root/mod/mod/orbit/pytest'

    def forward(self, **kwargs):
        """Default entry point."""
        if 'path' in kwargs or 'args' in kwargs:
            return self.run(path=kwargs.get('path'), args=kwargs.get('args'))
        return self.info()

    def run(self, path=None, args=None, timeout=120):
        """Execute pytest and return structured results."""
        target = path or self.path
        cmd = ['python', '-m', 'pytest', target, '--tb=short', '-q']
        if args:
            if isinstance(args, str):
                import shlex
                cmd += shlex.split(args)
            else:
                cmd += list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {'exit_code': -1, 'error': 'timeout', 'output': '', 'passed': 0, 'failed': 0, 'errors': 0}
        output = result.stdout + result.stderr
        passed = failed = errors = 0
        for line in output.splitlines():
            m_line = re.search(r'(\d+) passed', line)
            if m_line:
                passed = int(m_line.group(1))
            m_line = re.search(r'(\d+) failed', line)
            if m_line:
                failed = int(m_line.group(1))
            m_line = re.search(r'(\d+) error', line)
            if m_line:
                errors = int(m_line.group(1))
        return {
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'exit_code': result.returncode,
            'output': output,
        }

    def info(self):
        """Return module info."""
        return {
            'name': 'pytest',
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
