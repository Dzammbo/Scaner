"""Publish only this run's snapshot, retrying concurrent main updates."""
import os
import subprocess
import tempfile
from pathlib import Path

def git(*args):
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout.strip()

def main():
    source = Path(os.environ['SNAPSHOT_PATH'])
    if source.parent != Path('snapshots/live') or source.suffix != '.json':
        raise ValueError('Unexpected snapshot path')
    data = source.read_bytes()
    with tempfile.TemporaryDirectory(prefix='live-publish-') as directory:
        work = Path(directory) / 'repo'
        git('worktree', 'add', '--detach', str(work), 'HEAD')
        try:
            for attempt in range(3):
                git('fetch', 'origin', 'main')
                git('-C', str(work), 'reset', '--hard', 'origin/main')
                destination = work / source
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                git('-C', str(work), 'add', '--', str(source))
                if not git('-C', str(work), 'diff', '--cached', '--name-only'):
                    print('LIVE_HISTORY_SAVED=' + str(source))
                    return
                git('-C', str(work), '-c', 'user.name=github-actions[bot]',
                    '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                    'commit', '-m', 'Save on-demand live snapshot')
                try:
                    git('-C', str(work), 'push', 'origin', 'HEAD:main')
                    print('LIVE_HISTORY_SAVED=' + str(source))
                    return
                except subprocess.CalledProcessError:
                    if attempt == 2:
                        raise
        finally:
            git('worktree', 'remove', '--force', str(work))

if __name__ == '__main__':
    main()
