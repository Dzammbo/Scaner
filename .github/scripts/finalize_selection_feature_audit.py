"""League-controlled sensitivity, compact evidence exports and executed companion notebook."""
import datetime as dt,hashlib,importlib.util,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
import nbformat
from nbclient import NotebookClient
import selection_feature_audit as a
import selection_feature_validation as v
def main():
 root=Path(os.environ['COLLECTOR_ROOT']).resolve();control=Path(os.environ['CONTROL_ROOT']).resolve();source=root/'research/selection-feature-audit/2026-10-24/40115730421-1'
 for attempt in range(30):
  if (source/'results.json').exists():break
  subprocess.run(['git','fetch','origin','research/selection-lab-20261018','--quiet'],cwd=root,check=True)
  subprocess.run(['git','reset','--hard','origin/research/selection-lab-20261018','--quiet'],cwd=root,check=True)
  time.sleep(15)
 else:raise RuntimeError('Parent feature audit not ready')
 raw=(source/'original-entries.json.gz').read_bytes();rows=a.read(source/'original-entries.json.gz');report=a.read(source/'results.json');adjusted,predictions=v.run(rows,a.stamp(report['maturity_cutoff_utc']))
 validation=dict(input_sha256=hashlib.sha256(raw).hexdigest(),parent_rules_revision=report['rules_revision'],method='Same fixed temporal folds and ridge20; add country and training-only league indicators (>=8 training observations).',strict_league_adjusted_walk_forward=adjusted)
 run=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'];dest='research/selection-feature-audit/2026-10-24/'+run+'/'
 enc=lambda x:(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n').encode()
 scope={k:report[k]for k in ['generated_at_utc','source_revision','rules_revision','quality','baseline_all_original','baseline_mature','baseline_exact_mature','maturity_cutoff_utc','series','versions','by_price_basis','feature_inventory','methodology']}
 files={dest+'quality-scope.json':enc(scope),dest+'univariate-exact.json':enc(report['univariate_exact_mature']),dest+'univariate-all.json':enc(report['univariate_mature']),dest+'matched.json':enc(report['matched']),dest+'model-comparison.json':enc(dict(strict=report['strict_walk_forward'],retrospective=report['retrospective_walk_forward'])),dest+'league-adjusted-results.json':enc(validation)}
 # Actual original Top3/5/10 limits are read from original manual snapshots, never inferred from output size.
 bylimit={};snapshots={};unknown=[]
 for r in rows:
  limit=5 if r['series']!='manual_original'else None
  if r['series']=='manual_original'and r.get('sid'):
   p=root/'snapshots/live'/(r['sid']+'.json')
   if p.exists():
    if r['sid']not in snapshots:snapshots[r['sid']]=a.read(p).get('final_sports_selection_limit')
    limit=snapshots[r['sid']]
  r['original_selection_limit']=limit
  if limit is None:unknown.append(r)
  else:bylimit.setdefault(str(limit),[]).append(r)
 files[dest+'selection-limits.json']=enc(dict(groups={k:a.stats(z)for k,z in bylimit.items()},unknown=a.stats(unknown),note='Different dates and admission versions; descriptive, not causal comparison of quotas.'))
 with tempfile.TemporaryDirectory()as folder:
  work=Path(folder)
  for path,data in files.items():(work/Path(path).name).write_bytes(data)
  nb=nbformat.v4.new_notebook();nb.metadata.kernelspec=dict(name='python3',display_name='Python 3',language='python')
  nb.cells=[nbformat.v4.new_markdown_cell('# Аудит признаков отбора Top‑3/5/10\n\n## Краткий вывод\nИсторическая проверка исходных решений, с разделением прогнозирования голов и доходности точной линии. Числа ниже рассчитаны из сохранённых исходников; это наблюдательный анализ, не доказательство причинности и не будущий независимый тест.'),
   nbformat.v4.new_markdown_cell('## Контекст и метод\nОдин исходный вход на матч. Первый выбор без цены не заменяется поздним. В строгой проверке обучающие исходы должны быть записаны до начала проверяемого дня. Сопоставление учитывает минуту, счёт, линию, коэффициент, версию, страну и доступные ранее лиги. Пропуски имеют отдельные признаки. Источники: родительский results.json и original-entries.json.gz; SHA256 входа записан в league-adjusted-results.json.'),
   nbformat.v4.new_code_cell("import json\nfrom pathlib import Path\nimport numpy as np\nimport matplotlib.pyplot as plt\nscope=json.loads(Path('quality-scope.json').read_text())\nvalidation=json.loads(Path('league-adjusted-results.json').read_text())\nmodels=validation['strict_league_adjusted_walk_forward']['models']\nprint('Период:',scope['quality']['earliest_entry_utc'],'-',scope['quality']['latest_entry_utc'])\nprint('Исходных уникальных матчей:',scope['quality']['deduplicated_original_events'])\nprint('Зрелая выборка с точной ценой:',scope['baseline_exact_mature']['n'])"),
   nbformat.v4.new_markdown_cell('## Результаты\n### Признаки сверх линии, цены и контекста\nПоложительное изменение означает более точный прогноз оставшихся голов. Интервалы получены повторной выборкой целых дней; они описательные.'),
   nbformat.v4.new_code_cell("labels={'cumulative_shots':'Накопленные удары','fresh_shots':'Свежие удары','continuation':'Продолжение угрозы','territorial':'Опасные атаки и угловые','xg':'xG','historical_profile':'Исторический профиль','sports_rank':'Спортивный ранг','clock_and_cards':'Время и удаления'}\nkeys=[k for k in labels if models[k].get('n')]\nkeys.sort(key=lambda k:models[k]['nll_improvement_over_context'])\ny=np.arange(len(keys));vals=np.array([models[k]['nll_improvement_over_context'] for k in keys])\nfig,ax=plt.subplots(figsize=(10,5))\nax.barh(y,vals,color=['#287d63' if v>0 else '#b44c4c' for v in vals])\nfor i,k in enumerate(keys):\n lo,hi=models[k]['day_bootstrap_improvement_ci95'];ax.plot([lo,hi],[i,i],color='#222222',linewidth=1.4)\nax.set_yticks(y,[labels[k] for k in keys]);ax.axvline(0,color='#333333',linewidth=.8);ax.set_xlabel('Улучшение средней логарифмической ошибки относительно контекста')\nax.set_title('Строгая проверка на следующих днях с учётом лиг');fig.tight_layout();fig.savefig('family-validation.png',dpi=150);plt.show()\nfor k in reversed(keys):\n m=models[k];print(labels[k], 'N=',m['n'],'Δ=',round(m['nll_improvement_over_context'],4),'q=',round(m.get('day_level_q_bh',1),3))"),
   nbformat.v4.new_markdown_cell('### Рост оценки качества и фактическая доходность\nФиксированные диапазоны прогноза прямой доходности полной модели. Это результаты только на последующих днях, без подбора порогов по проверяемому дню.'),
   nbformat.v4.new_code_cell("bins=models['all_sports'].get('direct_expected_return_bins',[])\nbins=[b for b in bins if b['n']]\nfig,ax=plt.subplots(figsize=(9,4));x=np.arange(len(bins))\nax.bar(x-.18,[100*b['predicted_roi'] for b in bins],width=.36,label='Прогноз')\nax.bar(x+.18,[100*b['actual_roi'] for b in bins],width=.36,label='Факт')\nax.set_xticks(x,[str(b['lower'])+'…'+str(b['upper'])+'\\nN='+str(b['n']) for b in bins]);ax.axhline(0,color='#333333',linewidth=.8);ax.set_ylabel('ROI, %');ax.set_title('Соответствует ли более высокая оценка лучшему результату');ax.legend();fig.tight_layout();fig.savefig('return-calibration.png',dpi=150);plt.show()"),
   nbformat.v4.new_markdown_cell('## Ограничения и выводы\nАрхив начинается 13 октября. Результаты разных лимитов и версий относятся к разным календарным матчам. Часть рангов - порядок показа, они не смешиваются со спортивными рангами. Устаревшие, неизвестные и незавершённые исходы не становятся проигрышами. Поправка на множественные проверки приближённая. Полная реконструкция неполученных альтернатив и незафиксированных человеческих решений невозможна. Производственные правила и 20 независимых карманов этим аудитом не меняются.')]
  nbformat.validate(nb);nb=NotebookClient(nb,timeout=180,kernel_name='python3',resources={'metadata':{'path':str(work)}}).execute();nbformat.validate(nb)
  files[dest+'Аудит признаков отбора.ipynb']=nbformat.writes(nb).encode()
  for name in ['family-validation.png','return-calibration.png']:files[dest+name]=(work/name).read_bytes()
 spec=importlib.util.spec_from_file_location('save',control/'.github/scripts/refresh_live_results.py');saver=importlib.util.module_from_spec(spec);spec.loader.exec_module(saver);rev=saver.save_files(root,files)
 print('VALIDATED_FEATURE_AUDIT_PATH='+dest+'quality-scope.json',flush=True);print('VALIDATED_FEATURE_AUDIT_COMMIT='+rev,flush=True)
if __name__=='__main__':main()
