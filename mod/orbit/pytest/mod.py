import os
import re
import shlex
import subprocess
import sys
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
        cmd = [sys.executable, '-m', 'pytest', target, '--tb=short', '-q']
        if args:
            if isinstance(args, str):
                cmd += shlex.split(args)
            else:
                cmd += list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {'exit_code': -1, 'error': 'timeout', 'output': '', 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}
        output = result.stdout + result.stderr
        passed = failed = errors = skipped = 0
        duration = None
        # Find the single pytest summary line by its timing marker (only appears there)
        summary_line = None
        for line in output.splitlines():
            if re.search(r'\d+ (passed|failed|error|skipped)', line) and re.search(r'in \d+\.?\d*s', line):
                summary_line = line
                break
        if summary_line:
            m_p = re.search(r'(\d+) passed', summary_line)
            if m_p:
                passed = int(m_p.group(1))
            m_f = re.search(r'(\d+) failed', summary_line)
            if m_f:
                failed = int(m_f.group(1))
            m_e = re.search(r'(\d+) error', summary_line)
            if m_e:
                errors = int(m_e.group(1))
            m_s = re.search(r'(\d+) skipped', summary_line)
            if m_s:
                skipped = int(m_s.group(1))
            m_d = re.search(r'in (\d+\.?\d*)s', summary_line)
            if m_d:
                duration = float(m_d.group(1))
        failures = [
            line.split(' - ')[0][len('FAILED '):].strip()
            for line in output.splitlines()
            if line.startswith('FAILED ')
        ]
        return {
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'skipped': skipped,
            'failures': failures,
            'exit_code': result.returncode,
            'output': output,
            'duration': duration,
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
