import argparse
import json
from collections import Counter
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('run_directory');a=p.parse_args()
    path=Path(a.run_directory)
    r=[json.loads(l) for l in (path/'controller.jsonl').read_text().splitlines() if l.strip()]
    # Restrict counts to actual workload; retain full file for startup/tail analysis.
    manifest=path/'manifest.json'
    if manifest.exists():
        m=json.loads(manifest.read_text());r=[x for x in r if m['workload_started_timestamp']<=x['timestamp']<m['workload_finished_timestamp']]
    counts=Counter(x['kind'] for x in r)
    s=[x for x in r if x['kind']=='sample'];d=[x for x in r if x['kind']=='decision_created']
    out=dict(kind_counts=dict(counts),samples=len(s),invalid_samples=sum(x.get('cpu') is None for x in s),samples_overlapping_actions=sum(x.get('overlaps_action',False) for x in s),max_sample_interval_seconds=max((x.get('sample_interval_seconds') or 0 for x in s),default=None),confirmation_to_decision_seconds=[x['confirmation_to_decision_seconds'] for x in d],action_failures=sum(x['kind']=='action_finished' and not x['success'] for x in r),scope='workload window if manifest exists, otherwise full controller log',note='Query/event counts are controller activity, not total CPU overhead. Shared monitor work is reported separately.')
    (path/'analysis.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2))
if __name__=='__main__':main()
