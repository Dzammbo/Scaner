"""Save only isolated research files; preserve concurrent TB commits."""
import subprocess, tempfile
from pathlib import Path

def git(*args):
    return subprocess.run(['git',*args],check=True,capture_output=True,text=True).stdout.strip()
files={str(p):p.read_bytes() for p in Path('research/tm/data').rglob('*') if p.is_file()}
with tempfile.TemporaryDirectory() as d:
    work=Path(d)/'repo';git('worktree','add','--detach',str(work),'HEAD')
    try:
        for attempt in range(3):
            git('fetch','origin','main');git('-C',str(work),'reset','--hard','origin/main')
            for name,data in files.items():
                p=work/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            git('-C',str(work),'add','--','research/tm/data')
            if not git('-C',str(work),'diff','--cached','--name-only'):break
            git('-C',str(work),'-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com','commit','-m','Save isolated TM research data')
            try:git('-C',str(work),'push','origin','HEAD:main');break
            except subprocess.CalledProcessError:
                if attempt==2:raise
    finally:git('worktree','remove','--force',str(work))
