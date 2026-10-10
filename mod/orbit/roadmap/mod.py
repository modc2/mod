import os
import json
import mod as m

_DATA = os.path.join(os.path.dirname(__file__), 'roadmap.json')

VALID_STATUSES = {'planned', 'in-progress', 'done'}

_SEED = [
    {'id': 1, 'title': 'JSON-backed item store', 'status': 'done',
     'description': 'Persist roadmap items in roadmap.json with add/list/update.'},
    {'id': 2, 'title': 'REST API endpoint', 'status': 'planned',
     'description': 'Expose items/add/update over HTTP via the module gateway.'},
    {'id': 3, 'title': 'Status filter', 'status': 'planned',
     'description': 'Allow filtering items by status (planned, in-progress, done).'},
]


def _load():
    if not os.path.exists(_DATA):
        _save(_SEED)
        return list(_SEED)
    with open(_DATA) as f:
        return json.load(f)


def _save(items):
    with open(_DATA, 'w') as f:
        json.dump(items, f, indent=2)


class Mod:
    description = """roadmap"""
    path = r'/root/mod/mod/orbit/roadmap'

    def forward(self, **kwargs):
        method = kwargs.pop('method', 'items')
        if method == 'add':
            if 'title' not in kwargs:
                return {'error': 'title is required'}
            return self.add(**kwargs)
        if method == 'update':
            try:
                item_id = int(kwargs.pop('id'))
            except (KeyError, ValueError):
                return {'error': 'id is required and must be an integer'}
            return self.update(item_id, **kwargs)
        if method == 'delete':
            try:
                item_id = int(kwargs['id'])
            except (KeyError, ValueError):
                return {'error': 'id is required and must be an integer'}
            return self.delete(item_id)
        return self.items(status=kwargs.get('status'))

    def items(self, status=None):
        """Return roadmap items, optionally filtered by status (planned, in-progress, done)."""
        all_items = _load()
        if status is None:
            return all_items
        return [i for i in all_items if i.get('status') == status]

    def add(self, title, description='', status='planned'):
        """Append a new item and return it."""
        if status not in VALID_STATUSES:
            return {'error': 'invalid status', 'valid': sorted(VALID_STATUSES)}
        all_items = _load()
        next_id = max((i['id'] for i in all_items), default=0) + 1
        item = {'id': next_id, 'title': title, 'status': status,
                'description': description}
        all_items.append(item)
        _save(all_items)
        return item

    def update(self, id, **fields):
        """Patch an existing item by id and return it."""
        all_items = _load()
        for item in all_items:
            if item['id'] == id:
                if 'status' in fields and fields['status'] not in VALID_STATUSES:
                    return {'error': 'invalid status', 'valid': sorted(VALID_STATUSES)}
                for k, v in fields.items():
                    if k in ('title', 'status', 'description'):
                        item[k] = v
                _save(all_items)
                return item
        return {'error': 'not found', 'id': id}

    def delete(self, id: int):
        """Remove an item by id and return it, or an error dict if not found."""
        all_items = _load()
        for i, item in enumerate(all_items):
            if item['id'] == id:
                removed = all_items.pop(i)
                _save(all_items)
                return removed
        return {'error': 'not found', 'id': id}

    def info(self):
        """Return module info."""
        return {
            'name': 'roadmap',
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
