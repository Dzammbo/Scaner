"""Build one immutable model from frozen evidence, with purged time blocks."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import quality_forward as q

POINTS='research/all-entry-audit/2026-10-24/40067569187-1/entry-points.json.gz'
DIGEST='b5a17fb390b11bb34562335a89b360f005631f0eebcf8ce507bff4ed02ed58bd'


def prepare(root):
    raw=(root/POINTS).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=DIGEST:
        raise ValueError('Frozen training input changed')
    points=q.read(root/POINTS)
    confirmations={}
    for folder in ('research/live-background/primary/outcomes',
                   'research/live-background/experimental/outcomes','research/live-lab/outcomes'):
        for path in (root/folder).rglob('*.json*'):
            record=q.read(path)
            for outcome in record.get('events',[]):
                if not outcome.get('settled') or outcome.get('result_conflict'):
                    continue
                final=outcome.get('final')
                if not isinstance(final,list) or len(final)!=2:
                    continue
                try:
                    when=q.stamp(outcome['checked_at_utc'])
                except (ValueError,KeyError,TypeError):
                    continue
                key=(str(outcome['event_id']),tuple(final))
                confirmations[key]=min(when,confirmations.get(key,when))
    first={}
    for row in sorted(points,key=lambda r:(r['t'],r['order'])):
        ratio=q.num(row.get('gp_ratio'))
        if row['basis']!='exact_provider_main' or row.get('version')!='sports-only-2026-10-18-v8-fresh-first' or not row.get('strong') or ratio is None or not 0<=ratio<1.1:
            continue
        if not 8<=dt.datetime.fromtimestamp(row['t'],q.MSK).hour<24:
            continue
        try:
            q.vector(row)
        except (ValueError,TypeError,KeyError):
            continue
        # Freeze the first eligible observation before examining its final outcome.
        first.setdefault(row['id'],row)
    rows=[];counts=dict(first_eligible=len(first),unconfirmed=0,confirmation_clock_missing=0)
    for row in first.values():
        if row.get('profit') is None or not row.get('closed') or not isinstance(row.get('final'),list):
            counts['unconfirmed']+=1;continue
        key=(row['id'],tuple(row['final']))
        if key not in confirmations:
            counts['confirmation_clock_missing']+=1;continue
        if any(a<b for a,b in zip(row['final'],row['score'])):
            continue
        r=dict(row,additional=sum(row['final'])-sum(row['score']),confirmed_at=confirmations[key])
        if r['confirmed_at']<r['t']:
            raise ValueError('Outcome predates its own observation')
        rows.append(r)
    return rows,counts


def main():
    root=Path(os.environ['COLLECTOR_ROOT']).resolve()
    control=Path(os.environ['CONTROL_ROOT']).resolve()
    destination=root/q.MODEL_PATH
    if destination.exists():
        model=q.read(destination)
        assert model['version']==q.VERSION and model['protocol']==q.PROTOCOL
        print('QUALITY_MODEL_STATUS=already_frozen');return
    rows,counts=prepare(root)
    source=dict(points_path=POINTS,points_sha256=DIGEST,
                source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
                training_population='first exact-priced strong 8-60 entry per match; 08-24 Moscow; profile <1.1',
                counts=counts,confirmation_clocks='earliest matching settled nonconflicting saved provider outcome')
    artifact=q.train_model(rows,source)
    data=q.encode(artifact)
    digest=hashlib.sha256(data).hexdigest()
    cfg=dict(enabled=True,id=q.EXPERIMENT,version=q.VERSION,model_path=q.MODEL_PATH,
             model_sha256=digest,protocol=q.PROTOCOL,mode='prospective_parallel_comparison',
             activation='first_successful_background_capture_after_model_creation')
    report=dict(version=q.VERSION,model_sha256=digest,source=source,
                split_windows=artifact['split_windows'],calibration_before=artifact['calibration_before'],
                calibration_after=artifact['calibration_after'],historical_validation=artifact['historical_validation'],
                primary_selection_changed=False,prospective_results_not_available=True)
    files={q.MODEL_PATH:data,'.github/live-quality-experiment.json':q.encode(cfg),
           'research/selection-quality/models/remaining-goals-quality-v1-20261024-training.json':q.encode(report)}
    spec=importlib.util.spec_from_file_location('save_quality_model',control/'.github/scripts/refresh_live_results.py')
    saver=importlib.util.module_from_spec(spec);spec.loader.exec_module(saver)
    revision=saver.save_files(root,files)
    print('QUALITY_MODEL_STATUS=frozen')
    print('QUALITY_MODEL_COMMIT='+revision)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
