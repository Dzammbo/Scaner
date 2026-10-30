"""Materialize verified compact journals from a persistent cache and Git blobs.

The Git tree is the authority for paths and blob IDs. Cache entries are derived
projections, never a replacement for a missing or damaged source blob.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

from statistics_inputs import VERSION, decode, project

PREFIXES = ('research/live-lab', 'research/live-background',
            'research/live-results-refresh', 'research/shadow-analysis')
INDEX_VERSION = 'statistics-history-index-v1'


def git(root, *args, input=None):
    return subprocess.run(['git', *args], cwd=root, input=input,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def tree(root):
    result = {}
    for entry in git(root, 'ls-tree', '-r', '-z', 'HEAD', '--', *PREFIXES).split(b'\0'):
        if entry:
            metadata, name = entry.split(b'\t', 1)
            if not name.endswith((b'.json', b'.json.gz')):
                continue
            mode, kind, sha = metadata.split()
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                raise ValueError('Unexpected history tree entry')
            result[name.decode()] = sha.decode()
    if not result or not any('/receipts/' in name for name in result):
        raise ValueError('Empty history tree')
    return result


def encoded_projection(name, raw):
    data = project(name, decode(gzip.decompress(raw) if name.endswith('.gz') else raw))
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()


def cache_key(name, sha):
    kind = 'snapshot' if 'snapshots' in Path(name).parts else 'report' if 'live-results-refresh' in Path(name).parts else 'journal'
    return VERSION + ':' + kind + ':' + sha


def blob_sha(raw):
    # Git's SHA-1 object format; all source tree blob IDs are 40 hex characters.
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def hydrate(root, directory):
    started = time.monotonic()
    current = tree(root)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / 'history-index.json'
    db_path = directory / (VERSION + '.sqlite')
    # A missing or incomplete manifest triggers a complete, authoritative checkout.
    try:
        manifest = json.loads(manifest_path.read_text())
        if manifest['version'] != INDEX_VERSION or not isinstance(manifest['objects'], dict):
            raise ValueError('Index version mismatch')
        if not set(manifest['objects']) <= set(current):
            raise ValueError('History was removed')
        db = sqlite3.connect(db_path)
        # Open read-only in spirit: no projection is accepted without a validated source.
        db.execute('SELECT 1 FROM objects LIMIT 1').fetchone()
        reused = changed = 0
        for name, sha in current.items():
            item = manifest['objects'].get(name)
            value = None
            if item and item['sha'] == sha:
                row = db.execute('SELECT value FROM objects WHERE key=?', (cache_key(name, sha),)).fetchone()
                if row and hashlib.sha256(row[0]).hexdigest() == item['projection_sha256']:
                    value = gzip.decompress(row[0])
                    decode(value)  # Detect corrupted or incompatible cached JSON.
                    reused += 1
            if value is None:
                raw = git(root, 'cat-file', 'blob', sha)
                # Git verifies the object hash when reading it from the promisor remote.
                if blob_sha(raw) != sha:
                    raise ValueError('Git blob verification failed: ' + name)
                value = encoded_projection(name, raw)
                encoded = gzip.compress(value, compresslevel=1, mtime=0)
                db.execute('INSERT OR REPLACE INTO objects VALUES (?, ?)',
                           (cache_key(name, sha), encoded))
                changed += 1
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(gzip.compress(value, compresslevel=1, mtime=0)
                                    if name.endswith('.gz') else value)
        db.commit(); db.close()
        # Entries obtained from Git are added to the durable index only after refresh.
        print('HISTORY_INDEX=' + json.dumps(dict(mode='incremental', reused=reused,
              changed=changed, seconds=round(time.monotonic()-started, 3))), flush=True)
        return
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error,
            subprocess.CalledProcessError, EOFError) as exc:
        print('HISTORY_INDEX_FALLBACK=' + type(exc).__name__ + ':' + str(exc), flush=True)
    finally:
        if 'db' in locals():
            try: db.close()
            except sqlite3.Error: pass
    # A partial hydration must never be mixed with a full fallback checkout.
    for prefix in PREFIXES:
        shutil.rmtree(root/prefix, ignore_errors=True)
    git(root, 'sparse-checkout', 'add', *PREFIXES)
    for name in current:
        if not (root/name).is_file():
            raise ValueError('Full checkout did not provide ' + name)
    print('HISTORY_INDEX=' + json.dumps(dict(mode='full', count=len(current),
          seconds=round(time.monotonic()-started, 3))), flush=True)


def build(root, directory):
    started = time.monotonic()
    current = tree(root)
    # The collector may have appended new immutable journals while refresh ran.
    if any(not (root/name).is_file() for name in current):
        hydrate(root, directory)
        current = tree(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (VERSION + '.sqlite')
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS objects (key TEXT PRIMARY KEY, value BLOB NOT NULL)')
    try:
        old = json.loads((directory/'history-index.json').read_text())
        reusable = old['objects'] if old['version'] == INDEX_VERSION else {}
    except (OSError, KeyError, ValueError, TypeError):
        reusable = {}
    objects = {}
    for name, sha in current.items():
        source = root/name
        if not source.is_file():
            raise ValueError('Incomplete history: ' + name)
        previous = reusable.get(name, {})
        if previous.get('sha') == sha:
            row = db.execute('SELECT value FROM objects WHERE key=?', (cache_key(name, sha),)).fetchone()
            if row and hashlib.sha256(row[0]).hexdigest() == previous.get('projection_sha256'):
                objects[name] = previous
                continue
        raw = source.read_bytes()
        # Hydrated entries are projections, so only original Git bytes have the blob SHA.
        original = blob_sha(raw) == sha
        row = db.execute('SELECT value FROM objects WHERE key=?', (cache_key(name, sha),)).fetchone()
        if original:
            value = encoded_projection(name, raw)
            encoded = gzip.compress(value, compresslevel=1, mtime=0)
            db.execute('INSERT OR REPLACE INTO objects VALUES (?, ?)', (cache_key(name, sha), encoded))
        elif row:
            encoded = row[0]
            value = gzip.decompress(encoded)
            expected = gzip.compress(value, compresslevel=1, mtime=0) if name.endswith('.gz') else value
            if raw != expected:
                raise ValueError('Hydrated projection differs: ' + name)
        else:
            raise ValueError('Cannot verify source or cached projection: ' + name)
        objects[name] = dict(sha=sha, projection_sha256=hashlib.sha256(encoded).hexdigest())
    db.commit(); db.close()
    manifest = dict(version=INDEX_VERSION, objects=objects)
    temporary = directory / 'history-index.json.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, separators=(',', ':')))
    os.replace(temporary, directory/'history-index.json')
    print('HISTORY_INDEX=' + json.dumps(dict(mode='built', count=len(objects),
          seconds=round(time.monotonic()-started, 3))), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('hydrate', 'build'))
    parser.add_argument('root', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    globals()[args.operation](args.root.resolve(), args.directory.resolve())
