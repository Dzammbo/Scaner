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


def tracked_scope(root):
    path = root / '.github/live-tracked-groups.json'
    if not path.exists():
        return None
    cfg = json.loads(path.read_text())
    return cfg['activation_msk'][:10] if cfg.get('enabled') else None


def git(root, *args, input=None, env=None):
    return subprocess.run(['git', *args], cwd=root, input=input,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, env=env).stdout


def prefetch(root, shas):
    """Fetch missing immutable blobs together, avoiding one network roundtrip per file."""
    shas = sorted(set(shas))
    if not shas:
        return
    listing = git(root, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                  input=('\n'.join(shas) + '\n').encode(),
                  env=dict(os.environ, GIT_NO_LAZY_FETCH='1'))
    missing = [line.split()[0].decode() for line in listing.splitlines()
               if line.endswith(b' missing')]
    if missing:
        git(root, '-c', 'fetch.negotiationAlgorithm=noop', 'fetch', '--no-tags',
            '--no-write-fetch-head', '--filter=blob:none', 'origin', '--stdin',
            input=('\n'.join(missing) + '\n').encode())
        print('HISTORY_PREFETCH=' + json.dumps(dict(missing_blobs=len(missing))), flush=True)


def tree(root):
    result = {}
    since = tracked_scope(root)
    for entry in git(root, 'ls-tree', '-r', '-z', 'HEAD', '--', *PREFIXES).split(b'\0'):
        if entry:
            metadata, name = entry.split(b'\t', 1)
            if not name.endswith((b'.json', b'.json.gz')):
                continue
            mode, kind, sha = metadata.split()
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                raise ValueError('Unexpected history tree entry')
            path = name.decode()
            if since:
                parts = Path(path).parts
                allowed = (('receipts' in parts and parts[-2] == '2026-11-04') or 'under-pocket-receipts' in parts or 'tracked-group-receipts' in parts or 'tracked-sot-receipts' in parts or 'research-foundation-receipts' in parts or 'outcomes' in parts
                           or 'live-results-refresh' in parts)
                if not allowed or len(parts) < 3 or parts[-2] < since:
                    continue
            result[path] = sha.decode()
    if not since and (not result or not any('/receipts/' in name for name in result)):
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
        prefetch(root, [sha for name, sha in current.items()
                        if manifest['objects'].get(name, {}).get('sha') != sha])
        reused = changed = 0
        objects = {}
        for name, sha in current.items():
            item = manifest['objects'].get(name)
            value = None
            if item and item['sha'] == sha:
                row = db.execute('SELECT value FROM objects WHERE key=?', (cache_key(name, sha),)).fetchone()
                if row and hashlib.sha256(row[0]).hexdigest() == item['projection_sha256']:
                    encoded = row[0]
                    value = gzip.decompress(encoded)
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
            objects[name] = dict(sha=sha, projection_sha256=hashlib.sha256(encoded).hexdigest())
        db.commit(); db.close()
        temporary = directory / 'history-index.json.tmp'
        temporary.write_text(json.dumps(dict(version=INDEX_VERSION, objects=objects),
                                        ensure_ascii=False, separators=(',', ':')))
        os.replace(temporary, manifest_path)
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
    if tracked_scope(root):
        prefetch(root, current.values())
        # A new ten-group series needs only its compact journals and new outcomes.
        # Never hydrate the entire fitted historical snapshot archive for a reset.
        for name, sha in current.items():
            raw = git(root, 'cat-file', 'blob', sha)
            if blob_sha(raw) != sha:
                raise ValueError('Git blob verification failed: ' + name)
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
    else:
        git(root, 'sparse-checkout', 'add', *PREFIXES)
    for name in current:
        if not (root/name).is_file():
            raise ValueError('Full checkout did not provide ' + name)
    print('HISTORY_INDEX=' + json.dumps(dict(mode='full', count=len(current),
          seconds=round(time.monotonic()-started, 3))), flush=True)


def build(root, directory):
    started = time.monotonic()
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
    deferred = 0
    for name, sha in current.items():
        previous = reusable.get(name, {})
        if previous.get('sha') == sha:
            row = db.execute('SELECT value FROM objects WHERE key=?', (cache_key(name, sha),)).fetchone()
            if row and hashlib.sha256(row[0]).hexdigest() == previous.get('projection_sha256'):
                objects[name] = previous
                continue
        source = root/name
        if not source.is_file():
            # Reset after publication can remove hydrated files outside the
            # sparse cone; the verified DB entry above remains authoritative.
            deferred += 1
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
                # The branch can advance between reading and publishing the
                # report. Never assign its newer blob identity to old bytes.
                deferred += 1
                continue
        else:
            deferred += 1
            continue
        objects[name] = dict(sha=sha, projection_sha256=hashlib.sha256(encoded).hexdigest())
    db.commit(); db.close()
    manifest = dict(version=INDEX_VERSION, objects=objects)
    temporary = directory / 'history-index.json.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, separators=(',', ':')))
    os.replace(temporary, directory/'history-index.json')
    print('HISTORY_INDEX=' + json.dumps(dict(mode='built', count=len(objects), deferred=deferred,
          seconds=round(time.monotonic()-started, 3))), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('hydrate', 'build'))
    parser.add_argument('root', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    globals()[args.operation](args.root.resolve(), args.directory.resolve())
