"""Prospective pauses without changing frozen catalogs or historical journals."""
import json
from pathlib import Path

CONFIG = '.github/live-pocket-pauses.json'
FAMILIES = {'independent_pockets', 'distinct_pockets', 'seven_direction_pockets',
            'refined_pockets', 'external_pockets', 'strengthened_pockets',
            'hypothesis_pockets'}


def load(root):
    path = Path(root) / CONFIG
    if not path.exists():
        return {}
    cfg = json.loads(path.read_text())
    paused = cfg.get('paused')
    if cfg.get('schema_version') != 1 or not isinstance(paused, dict) or set(paused) - FAMILIES:
        raise ValueError('Invalid pocket pause policy')
    for section, ids in paused.items():
        if (not isinstance(ids, list) or any(not isinstance(key, str) or not key for key in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Invalid paused pocket identifiers: '+section)
    return paused


def ids(root, section):
    return set(load(root).get(section, []))
