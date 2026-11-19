"""Packaged, automatically discovered recipes; never downloads or executes helper scripts."""
import json
import re
import runtime as rt


def recipes():
    result = []
    for path in sorted((rt.SOURCE / 'recipes').glob('*.json')):
        if path.is_symlink(): raise ValueError('Recipes must be regular packaged files')
        item = json.loads(path.read_text())
        if not re.fullmatch(r'[a-z][a-z0-9-]*', item['id']) or item['id'] != path.stem:
            raise ValueError('Recipe identity must match its filename')
        if set(item) - {'id', 'title', 'description', 'module', 'settings'}: raise ValueError('Unknown recipe fields')
        if item.get('module'): rt.validate_modules({'recipes/' + item['id'] + '.nix': item['module']})
        result.append(item)
    return result
