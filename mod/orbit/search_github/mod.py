import os
import json
import urllib.request
import urllib.parse
import urllib.error
import mod as m

def _parse_rate_limit(headers):
    def _int(name):
        v = headers.get(name) if headers else None
        try:
            return int(v) if v is not None else None
        except (ValueError, TypeError):
            return None
    return {
        'limit': _int('X-RateLimit-Limit'),
        'remaining': _int('X-RateLimit-Remaining'),
        'reset': _int('X-RateLimit-Reset'),
    }

class Mod:
    description = """Search GitHub code (file contents) via the public /search/code API"""
    path = r'/root/mod/mod/orbit/search_github'

    def forward(self, **kwargs):
        """Default entry point."""
        if 'query' in kwargs:
            if kwargs.pop('repos', False):
                return self.search_repos(**kwargs)
            return self.search(**kwargs)
        return self.info()

    def search(self, query, language=None, repo=None, user=None, org=None, path=None, filename=None, extension=None, n=10, page=1, sort=None, order=None, **kwargs):
        """Search GitHub code. Returns list of {path, repository, url, fragment, fragments}.

        Qualifier parameters (all optional):
          language  — restrict to a programming language (e.g. 'python')
          repo      — restrict to a specific repo (e.g. 'owner/name')
          user      — restrict to all repos owned by a user login
          org       — restrict to all repos in an organisation
          path      — restrict to files under a directory path
          filename  — restrict to files with a specific name
          extension — restrict to files with a specific extension (e.g. 'yaml')

        Sort/order parameters (all optional):
          sort      — sort order; only 'indexed' is meaningful for code search
                      (most recently indexed first); omit for best-match order
          order     — 'asc' or 'desc' (default desc); only used when sort is set
        """
        q = query
        if language:
            q += f' language:"{language}"'
        if repo:
            q += f' repo:"{repo}"'
        if user:
            q += f' user:"{user}"'
        if org:
            q += f' org:"{org}"'
        if path:
            q += f' path:"{path}"'
        if filename:
            q += f' filename:"{filename}"'
        if extension:
            q += f' extension:"{extension}"'
        page = max(1, int(page))
        url = 'https://api.github.com/search/code?q=' + urllib.parse.quote(q) + f'&per_page={min(int(n), 100)}&page={page}'
        if sort:
            url += f'&sort={urllib.parse.quote(str(sort))}'
        if order:
            url += f'&order={urllib.parse.quote(str(order))}'
        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/vnd.github.text-match+json')
        req.add_header('User-Agent', 'search_github-mod/0.1')
        token = os.environ.get('GITHUB_TOKEN')
        if token:
            req.add_header('Authorization', f'Bearer {token}')
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                rate_limit = _parse_rate_limit(resp.headers)
        except urllib.error.HTTPError as e:
            rate_limit = _parse_rate_limit(e.headers)
            try:
                body = json.loads(e.read().decode('utf-8', errors='replace'))
                msg = body.get('message', e.reason)
            except Exception:
                msg = e.reason
            return {'error': True, 'status': e.code, 'message': msg, 'total_count': 0, 'incomplete_results': False, 'results': [], 'rate_limit': rate_limit}
        except urllib.error.URLError as e:
            return {'error': True, 'status': None, 'message': str(e.reason), 'total_count': 0, 'incomplete_results': False, 'results': [], 'rate_limit': None}
        results = []
        for item in data.get('items', []):
            matches = item.get('text_matches', [])
            fragments = [m.get('fragment', '') for m in matches]
            results.append({
                'path': item.get('path', ''),
                'repository': item.get('repository', {}).get('full_name', ''),
                'url': item.get('html_url', ''),
                'fragment': fragments[0] if fragments else '',
                'fragments': fragments,
            })
        return {
            'total_count': data.get('total_count', 0),
            'incomplete_results': data.get('incomplete_results', False),
            'results': results,
            'rate_limit': rate_limit,
        }

    def search_repos(self, query, language=None, user=None, org=None, topic=None, stars=None, forks=None, size=None, created=None, pushed=None, n=10, page=1, sort=None, order=None, **kwargs):
        """Search GitHub repositories via /search/repositories.

        Qualifier parameters (all optional):
          language — restrict to a programming language
          user     — restrict to repos owned by a user login
          org      — restrict to repos in an organisation
          topic    — restrict to repos with a specific topic
          stars    — filter by star count (e.g. '>100')
          forks    — filter by fork count (e.g. '>50')
          size     — filter by repo size in KB (e.g. '>1000')
          created  — filter by creation date (e.g. '>2020-01-01')
          pushed   — filter by last push date

        Sort/order parameters (all optional):
          sort  — 'stars', 'forks', 'help-wanted-issues', or 'updated'
          order — 'asc' or 'desc' (default desc)
        """
        q = query
        if language:
            q += f' language:"{language}"'
        if user:
            q += f' user:"{user}"'
        if org:
            q += f' org:"{org}"'
        if topic:
            q += f' topic:"{topic}"'
        if stars:
            q += f' stars:{stars}'
        if forks:
            q += f' forks:{forks}'
        if size:
            q += f' size:{size}'
        if created:
            q += f' created:{created}'
        if pushed:
            q += f' pushed:{pushed}'
        page = max(1, int(page))
        url = 'https://api.github.com/search/repositories?q=' + urllib.parse.quote(q) + f'&per_page={min(int(n), 100)}&page={page}'
        if sort:
            url += f'&sort={urllib.parse.quote(str(sort))}'
        if order:
            url += f'&order={urllib.parse.quote(str(order))}'
        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/vnd.github+json')
        req.add_header('User-Agent', 'search_github-mod/0.1')
        token = os.environ.get('GITHUB_TOKEN')
        if token:
            req.add_header('Authorization', f'Bearer {token}')
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                rate_limit = _parse_rate_limit(resp.headers)
        except urllib.error.HTTPError as e:
            rate_limit = _parse_rate_limit(e.headers)
            try:
                body = json.loads(e.read().decode('utf-8', errors='replace'))
                msg = body.get('message', e.reason)
            except Exception:
                msg = e.reason
            return {'error': True, 'status': e.code, 'message': msg, 'total_count': 0, 'incomplete_results': False, 'results': [], 'rate_limit': rate_limit}
        except urllib.error.URLError as e:
            return {'error': True, 'status': None, 'message': str(e.reason), 'total_count': 0, 'incomplete_results': False, 'results': [], 'rate_limit': None}
        results = []
        for item in data.get('items', []):
            results.append({
                'full_name': item.get('full_name', ''),
                'description': item.get('description', ''),
                'html_url': item.get('html_url', ''),
                'language': item.get('language', ''),
                'stargazers_count': item.get('stargazers_count', 0),
                'forks_count': item.get('forks_count', 0),
                'topics': item.get('topics', []),
                'updated_at': item.get('updated_at', ''),
                'archived': item.get('archived', False),
                'open_issues_count': item.get('open_issues_count', 0),
                'created_at': item.get('created_at', ''),
                'license': (item.get('license') or {}).get('spdx_id'),
            })
        return {
            'total_count': data.get('total_count', 0),
            'incomplete_results': data.get('incomplete_results', False),
            'results': results,
            'rate_limit': rate_limit,
        }

    def info(self):
        """Return module info."""
        return {
            'name': 'search_github',
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
