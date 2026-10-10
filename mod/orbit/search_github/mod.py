import os
import json
import urllib.request
import urllib.parse
import urllib.error
import mod as m

class Mod:
    description = """Search GitHub code (file contents) via the public /search/code API"""
    path = r'/root/mod/mod/orbit/search_github'

    def forward(self, **kwargs):
        """Default entry point."""
        if 'query' in kwargs:
            return self.search(**kwargs)
        return self.info()

    def search(self, query, language=None, repo=None, n=10, **kwargs):
        """Search GitHub code. Returns list of {path, repository, url, fragment}."""
        q = query
        if language:
            q += f' language:{language}'
        if repo:
            q += f' repo:{repo}'
        url = 'https://api.github.com/search/code?q=' + urllib.parse.quote(q) + f'&per_page={min(int(n), 100)}'
        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/vnd.github.text-match+json')
        req.add_header('User-Agent', 'search_github-mod/0.1')
        token = os.environ.get('GITHUB_TOKEN')
        if token:
            req.add_header('Authorization', f'Bearer {token}')
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return {'error': True, 'status': e.code, 'message': e.reason, 'total_count': 0, 'incomplete_results': False, 'results': []}
        except urllib.error.URLError as e:
            return {'error': True, 'status': None, 'message': str(e.reason), 'total_count': 0, 'incomplete_results': False, 'results': []}
        results = []
        for item in data.get('items', []):
            matches = item.get('text_matches', [])
            fragment = matches[0].get('fragment', '') if matches else ''
            results.append({
                'path': item.get('path', ''),
                'repository': item.get('repository', {}).get('full_name', ''),
                'url': item.get('html_url', ''),
                'fragment': fragment,
            })
        return {
            'total_count': data.get('total_count', 0),
            'incomplete_results': data.get('incomplete_results', False),
            'results': results,
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
