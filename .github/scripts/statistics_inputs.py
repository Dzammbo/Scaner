"""Refresh-only, content-addressed input cache. Source journals remain immutable."""
import collections
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

VERSION = 'statistics-inputs-v1'
FAMILIES = ('independent_pockets', 'distinct_pockets', 'seven_direction_pockets',
            'refined_pockets', 'external_pockets', 'strengthened_pockets', 'hypothesis_pockets')
SNAPSHOT_FIELDS = ('snapshot_id', 'board_observed_at_utc', 'code_revision', 'rules_version',
                   'research_version', 'trigger_main_sha')
EVENT_FIELDS = ('event_id', 'minute', 'score', 'home_id', 'away_id', 'league', 'country',
                'research_enriched', 'research_detail_coverage', 'stats_trend_shortlisted')


def project(path, data):
    # Only refresh consumers use this loader, never Live or research admission.
    if 'snapshots' in Path(path).parts:
        result = {key: data[key] for key in SNAPSHOT_FIELDS if key in data}
        result['events'] = []
        for event in data.get('events', []):
            row = {key: event[key] for key in EVENT_FIELDS if key in event}
            row['stats_trend'] = {'available': bool((event.get('stats_trend') or {}).get('available'))}
            result['events'].append(row)
        return result
    if 'live-results-refresh' in Path(path).parts:
        keys = ('generated_at_utc', 'statistics_epoch', 'population', 'current',
                'quality_comparison', 'shadow_universe', 'statistics_tables') + FAMILIES
        return {key: data[key] for key in keys if key in data}
    return data


class RefreshInputs:
    def __init__(self, root, directory=None):
        self.root = Path(root).resolve()
        self.counts = collections.Counter()
        self.memory = collections.OrderedDict()
        self.memory_bytes = 0
        self.db = None
        self.tracked = {}
        self.initial_stats = {}
        clean = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'research'],
                               cwd=self.root, capture_output=True).returncode == 0
        if clean:
            listing = subprocess.run(['git', 'ls-files', '-s', '-z', '--', 'research'],
                                     cwd=self.root, capture_output=True, check=True).stdout
            for record in listing.split(b'\0'):
                if record:
                    meta, path = record.split(b'\t', 1)
                    self.tracked[path.decode()] = meta.split()[1].decode()
            for name in self.tracked:
                path = self.root / name
                if path.exists():
                    stat = path.stat()
                    self.initial_stats[name] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if directory:
            directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
            path = directory / (VERSION + '.sqlite')
            try:
                self.db = sqlite3.connect(path)
                self.db.execute('CREATE TABLE IF NOT EXISTS objects (key TEXT PRIMARY KEY, value BLOB NOT NULL)')
            except sqlite3.DatabaseError:
                if self.db: self.db.close()
                path.unlink(missing_ok=True)
                self.db = sqlite3.connect(path)
                self.db.execute('CREATE TABLE objects (key TEXT PRIMARY KEY, value BLOB NOT NULL)')

    def load(self, path):
        path = Path(path).resolve()
        relative = path.relative_to(self.root).as_posix()
        stat = path.stat()
        local_key = (relative, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if local_key in self.memory:
            self.counts['memory_hits'] += 1
            data, size = self.memory[local_key]
            self.memory.move_to_end(local_key)
            return data
        raw = None
        digest = self.tracked.get(relative)
        if self.initial_stats.get(relative) != local_key[1:]:
            digest = None
        if digest is None:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
        kind = 'snapshot' if 'snapshots' in path.parts else 'report' if 'live-results-refresh' in path.parts else 'journal'
        key = VERSION + ':' + kind + ':' + digest
        encoded = None
        if self.db:
            try:
                row = self.db.execute('SELECT value FROM objects WHERE key=?', (key,)).fetchone()
                if row:
                    encoded = row[0]
                    data = json.loads(gzip.decompress(encoded))
                    self.counts['persistent_hits'] += 1
            except (sqlite3.DatabaseError, ValueError, OSError, EOFError):
                encoded = None
                self.counts['cache_errors'] += 1
        if encoded is None:
            raw = raw if raw is not None else path.read_bytes()
            data = project(path, json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw))
            self.counts['source_decodes'] += 1
            encoded = gzip.compress(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode(), compresslevel=1, mtime=0)
            if self.db:
                self.db.execute('INSERT OR REPLACE INTO objects VALUES (?, ?)', (key, encoded))
                if self.counts['source_decodes'] % 128 == 0: self.db.commit()
        size = max(len(encoded) * 5, 1024)
        self.memory[local_key] = (data, size); self.memory_bytes += size
        while self.memory_bytes > 64 * 1024 * 1024 and len(self.memory) > 1:
            _, (_, removed) = self.memory.popitem(last=False)
            self.memory_bytes -= removed
        return data

    def install(self, capture):
        capture.load_json = self.load
        for name in FAMILIES + ('shadow_universe', 'quality_forward'):
            module = importlib.import_module(name)
            for attr in ('read', 'load'):
                if hasattr(module, attr): setattr(module, attr, self.load)

    def close(self):
        if self.db:
            self.db.commit(); self.db.close(); self.db = None


class Stages:
    def __init__(self):
        self.start = self.last = time.monotonic()
        self.seconds = {}

    def mark(self, name):
        now = time.monotonic()
        self.seconds[name] = round(now - self.last, 3); self.last = now
        print('STATISTICS_STAGE=' + json.dumps(dict(stage=name, seconds=self.seconds[name],
              elapsed_seconds=round(now-self.start, 3))), flush=True)
