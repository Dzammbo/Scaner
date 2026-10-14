import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/save_live_snapshot.py'

class PublishTests(unittest.TestCase):
    def test_preserves_concurrent_main_and_only_saves_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);remote=root/'origin.git';repo=root/'runner';other=root/'other'
            def git(*args,cwd=None):
                return subprocess.run(['git',*map(str,args)],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()
            git('init','--bare',remote)
            git('clone',remote,repo)
            git('config','user.name','Test',cwd=repo);git('config','user.email','test@example.invalid',cwd=repo)
            git('checkout','-b','main',cwd=repo)
            (repo/'diary.txt').write_text('original')
            git('add','diary.txt',cwd=repo);git('commit','-m','initial',cwd=repo);git('push','-u','origin','main',cwd=repo)
            git('clone','--branch','main',remote,other)
            git('config','user.name','Test',cwd=other);git('config','user.email','test@example.invalid',cwd=other)
            (other/'diary.txt').write_text('concurrent update')
            git('add','diary.txt',cwd=other);git('commit','-m','concurrent',cwd=other);git('push',cwd=other)
            relative=Path('snapshots/live/test-1.json');p=repo/relative;p.parent.mkdir(parents=True);p.write_text('{"events":[]}')
            env=dict(os.environ,SNAPSHOT_PATH=str(relative))
            result=subprocess.run([sys.executable,str(SCRIPT)],cwd=repo,env=env,check=True,capture_output=True,text=True)
            self.assertIn('LIVE_HISTORY_SAVED=',result.stdout)
            git('fetch','origin','main',cwd=repo)
            self.assertEqual(git('show','origin/main:diary.txt',cwd=repo),'concurrent update')
            self.assertEqual(git('show','origin/main:'+str(relative),cwd=repo),'{"events":[]}')
            self.assertEqual(git('diff-tree','--no-commit-id','--name-only','-r','origin/main',cwd=repo),str(relative))

if __name__=='__main__':unittest.main()
