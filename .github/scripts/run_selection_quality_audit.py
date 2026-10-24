"""Read-only analysis of frozen inputs; publish only new immutable result files."""
import datetime as dt, hashlib, importlib.util, json, os, subprocess, sys
from pathlib import Path
root=Path(os.environ["COLLECTOR_ROOT"]).resolve()
control=Path(os.environ["CONTROL_ROOT"]).resolve()
paths=[
 "research/all-entry-audit/2026-10-24/40067569187-1/entry-points.json.gz",
 "research/live-results-refresh/2026-10-24/40063648567-1.json",
 "research/all-entry-audit/2026-10-22/39743379318-1/entry-points.json.gz"
]
source_revision=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
result=subprocess.run([sys.executable,str(control/".github/scripts/selection_quality_audit.py"),*[str(root/p) for p in paths]],capture_output=True,text=True,check=True)
sections={name:json.loads(body) for name,body in (line.split(" ",1) for line in result.stdout.splitlines())}
assert sections["FROZEN_OLD"]["n"]==573
assert abs(sections["FROZEN_OLD"]["net"]-28.7375)<1e-8
assert sections["PRIMARY"]["matched"]==224, sections["PRIMARY"]["missing"]
assert not sections["PRIMARY"]["missing"]
assert abs(sections["PRIMARY"]["baseline"]["net"]+4.9)<1e-8
out=dict(generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),source_revision=source_revision,rules_revision=os.environ["GITHUB_SHA"],source_paths=paths,sha256={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths},analysis=sections,live_selection_changed=False)
relative="research/selection-quality/2026-10-24/"+os.environ["GITHUB_RUN_ID"]+"-"+os.environ["GITHUB_RUN_ATTEMPT"]+"/results.json"
spec=importlib.util.spec_from_file_location("save",control/".github/scripts/refresh_live_results.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
revision=module.save_files(root,{relative:(json.dumps(out,ensure_ascii=False,indent=2)+"\n").encode()})
print("RESULT_PATH="+relative)
print("RESULT_COMMIT="+revision)
print(json.dumps({k:v for k,v in sections.items() if k in ("POCKET","FROZEN_OLD","FIXED_FILTERS")},ensure_ascii=False))
