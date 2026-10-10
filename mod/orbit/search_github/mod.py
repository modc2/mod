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
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode('utf-8', errors='replace'))
                msg = body.get('message', e.reason)
            except Exception:
                msg = e.reason
            return {'error': True, 'status': e.code, 'message': msg, 'total_count': 0, 'incomplete_results': False, 'results': []}
        except urllib.error.URLError as e:
            return {'error': True, 'status': None, 'message': str(e.reason), 'total_count': 0, 'incomplete_results': False, 'results': []}
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
