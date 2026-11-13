"""Recompute controller activity and collect resource metrics for one or more runs."""
import argparse
import csv
import json
from pathlib import Path
from collections import Counter


def analyze(folder):
    m=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    r=json.loads((folder/'resource_summary.json').read_text(encoding='utf-8'))
    s=json.loads((folder/'workload/summary.json').read_text(encoding='utf-8'))
    logs=[json.loads(l) for l in (folder/'controller.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    start,end=m['workload_started_timestamp'],m['workload_finished_timestamp']
    rows=[x for x in logs if start<=x['timestamp']<end]
    counts=Counter(x['kind'] for x in rows)
    calls=[x for x in logs if x['kind']=='docker_cli_resource' and x['started_timestamp']>=start and x['completed_timestamp']<=end]
    crossing=[x for x in logs if x['kind']=='docker_cli_resource' and x['started_timestamp']<end and x['completed_timestamp']>start and x not in calls]
    errors=[x for x in logs if x['kind']=='thread_error' or x['kind']=='action_finished' and not x['success']]
    accounted=all(x['cpu_seconds'] is not None for x in calls) and bool(calls)
    out=dict(run_id=m['run_id'],arm=m['arm'],profile=m['profile'],measurement_version=r['measurement_version'],
             controller_failed=bool(errors) or m.get('controller_exited_during_workload',True),
             successful_requests=s['successful_requests'],failed_requests=s['failed_requests'],
             duration_seconds=r['duration_seconds'],process_cpu_seconds=r['process_cpu_seconds'],
             process_mean_cpu_percent_one_core=r['process_mean_cpu_percent_one_core'],
             process_mean_rss_mib=r['process_mean_rss_mib'],process_sampled_peak_rss_mib=r['process_sampled_peak_rss_mib'],
             monitor_thread_cpu_seconds=r['thread_cpu_seconds'].get('monitor'),
             control_thread_cpu_seconds=r['thread_cpu_seconds'].get('control'),
             action_thread_cpu_seconds=r['thread_cpu_seconds'].get('worker'),
             controller_evaluations=counts['controller_evaluation'],samples=counts['sample'],
             docker_cli_calls_fully_inside_window=len(calls),docker_cli_calls_crossing_boundary=len(crossing),
             docker_cli_cpu_seconds_fully_inside_window=sum(x['cpu_seconds'] for x in calls) if accounted else None,
             docker_cli_cpu_accounting_complete=accounted,
             resource_samples=r['samples'],max_resource_sample_gap_seconds=r['max_sample_gap_seconds'])
    (folder/'resource_analysis.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('directories',nargs='+');p.add_argument('--csv');a=p.parse_args()
    rows=[analyze(Path(d)) for d in a.directories]
    if a.csv:
        target=Path(a.csv)
        with target.open('x',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
