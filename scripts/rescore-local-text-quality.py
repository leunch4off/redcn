"""Rescore recorded public probe outputs; never runs inference or changes weights.

Original native-run files remain intact. Versioned checker hashes make it clear
that this is a later development rescore, not a newly unseen benchmark.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_model_contract import content_sha,file_sha
from text_quality_probes import probes

spec=importlib.util.spec_from_file_location('paired_benchmark',ROOT/'scripts/benchmark-local-text-quality.py')
benchmark=importlib.util.module_from_spec(spec);spec.loader.exec_module(benchmark)


def rescore(paths):
    selected=probes();by_id={row['id']:row for row in selected};models=[]
    for path in paths:
        original=json.loads(Path(path).read_text())
        if original.get('probeSha256')!=content_sha(selected):raise ValueError('Probe sets do not match')
        rows=[]
        for row in original['rows']:
            if row.get('text') is not None:
                row={**row,'checks':benchmark.assess(by_id[row['id']],row['text'])}
                if row['status']=='incomplete':
                    row['checks']['passed']=False;row['checks']['issues'].append('Полный ответ не подтверждён')
            rows.append(row)
        models.append({**original,'originalReportSha256':file_sha(path),
            'originalChecksSummary':original['summary'],'rows':rows,'summary':benchmark.summary(rows),
            'styleDiagnostics':benchmark.style_diagnostics(rows,selected)})
    return {'version':2,'status':'development-rescore-unaccepted','probeSha256':content_sha(selected),
        'checkerSha256':{path.name:file_sha(path) for path in (ROOT/'cli/text_quality.py',ROOT/'cli/local_content.py')},
        'inferenceRepeated':False,'models':models,'automaticPromotion':False,'nativeLanguageReviewCompleted':False,
        'limitations':['Checks were refined after viewing outputs; these are development measurements, not unseen native-reviewed acceptance.',
            'Passing checks does not establish fluent, faithful or original language.','MLX peak memory is allocator peak, not whole-system RSS; per-probe latency excludes model startup.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reports',nargs='+',required=True)
    parser.add_argument('--output',required=True);args=parser.parse_args();target=Path(args.output)
    if target.exists():raise ValueError('Use a new filename to preserve previous measurements')
    value=rescore(args.reports);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'models':[{'model':row['model'],'adapterSha256':row.get('adapterSha256'),
        'summary':row['summary'],'styleDiagnostics':row['styleDiagnostics']} for row in value['models']]},ensure_ascii=False))
