"""Compare independent judges of exactly the same saved model responses."""
import argparse
import json
import math
import statistics
from pathlib import Path
from .cli import state, digest, MODELS
from .judge import report


def load(path):
    with state(path) as db:
        manifest = json.loads(db.execute('SELECT manifest FROM config').fetchone()[0])
        config = json.loads(db.execute('SELECT manifest FROM judge_config').fetchone()[0])
        responses = [tuple(r) for r in db.execute('SELECT id,qid,model,response FROM attempts ORDER BY id')]
        grades = {}
        for row in db.execute("SELECT a.qid,a.model,g.grade FROM grades g JOIN attempts a ON a.id=g.attempt_id WHERE g.status='done'"):
            key = (row['qid'], row['model'])
            if key in grades: raise ValueError('duplicate completed grade')
            grades[key] = json.loads(row['grade'])
        accounting = report(db)
        latencies = [r[0] for r in db.execute("SELECT latency_ms FROM grades WHERE status='done' AND latency_ms IS NOT NULL")]
        accounting['latency_completed_grades_ms'] = {
            'count': len(latencies), 'total': sum(latencies),
            'median': statistics.median(latencies) if latencies else None,
            'p95_nearest_rank': sorted(latencies)[math.ceil(0.95*len(latencies))-1] if latencies else None}
        return manifest, config, digest(responses), grades, accounting


def compare(reference, alternative):
    a, b = load(reference), load(alternative)
    if a[0] != b[0] or a[2] != b[2]:
        raise ValueError('judges did not receive identical saved generations')
    for key in ('prompt_sha256', 'schema_sha256', 'max_completion_tokens'):
        if a[1][key] != b[1][key]: raise ValueError('grading protocol differs')
    common = a[3].keys() & b[3].keys()
    result = {'reference_judge':a[1]['model'], 'alternative_judge':b[1]['model'],
        'responses_sha256':a[2], 'matched_grades':len(common),
        'reference_only_grades':len(a[3].keys()-b[3].keys()),
        'alternative_only_grades':len(b[3].keys()-a[3].keys()), 'models':{},
        'note':'Judge agreement is not ground-truth accuracy. This is a small pilot with few positive labels.',
        'reference_accounting':a[4], 'alternative_accounting':b[4]}
    for model in MODELS:
        keys = [key for key in common if key[1] == model]
        counts = {'both_correct':0, 'both_incorrect':0, 'reference_only_correct':0, 'alternative_only_correct':0}
        confidence_differences = 0
        for key in keys:
            x, y = a[3][key]['correct']=='yes', b[3][key]['correct']=='yes'
            label = 'both_correct' if x and y else 'both_incorrect' if not x and not y else 'reference_only_correct' if x else 'alternative_only_correct'
            counts[label] += 1
            confidence_differences += a[3][key]['confidence'] != b[3][key]['confidence']
        n = len(keys)
        positive_denom = 2*counts['both_correct']+counts['reference_only_correct']+counts['alternative_only_correct']
        result['models'][model] = dict(counts, matched=n,
            correctness_agreement=(counts['both_correct']+counts['both_incorrect'])/n if n else None,
            positive_agreement=2*counts['both_correct']/positive_denom if positive_denom else None,
            confidence_disagreements=confidence_differences)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('reference',type=Path);p.add_argument('alternative',type=Path)
    args=p.parse_args()
    try: print(json.dumps(compare(args.reference,args.alternative),indent=2))
    except Exception as e:
        print('Comparison failed: '+type(e).__name__);raise SystemExit(1)

if __name__=='__main__':main()
