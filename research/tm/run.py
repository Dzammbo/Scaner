"""Explicit request router for the isolated research workflow."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path('research/tm')
cfg=json.loads((ROOT/'request.json').read_text())
subprocess.run([sys.executable,'-m','unittest','discover','-s',str(ROOT),'-p','test_*.py','-q'],check=True)
mode=cfg.get('mode')
if mode in ('probe','collect'):
    subprocess.run([sys.executable,str(ROOT/(mode+'.py'))],check=True)
elif mode=='analyze':
    run=str(cfg.get('data_run',''))
    if not run.isdigit():raise ValueError('Only an isolated numeric research run ID is accepted')
    folder=ROOT/'data'/run
    subprocess.run([sys.executable,str(ROOT/'analyze.py'),str(folder)],check=True)
    subprocess.run([sys.executable,str(ROOT/'audit.py'),str(folder)],check=True)
    subprocess.run([sys.executable,str(ROOT/'report.py'),str(folder)],check=True)
else:raise ValueError('Unknown research mode; live rules are not activated')
