#!/usr/bin/env python3
"""Remove artifacts of older successful runs on the current workflow/branch."""
import argparse
import json
import os
import subprocess


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def pages(path, field):
    page = 1
    while True:
        items = api(f'{path}?per_page=100&page={page}')[field]
        yield from items
        if len(items) < 100:
            break
        page += 1


def eligible(run, current):
    return (run['conclusion'] == 'success'
            and run['head_branch'] == current['head_branch']
            and run['workflow_id'] == current['workflow_id']
            and run['run_number'] < current['run_number'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    base = 'repos/' + os.environ['GITHUB_REPOSITORY'] + '/actions'
    current = api(base + '/runs/' + os.environ['GITHUB_RUN_ID'])
    count = 0
    for run in pages(f"{base}/workflows/{current['workflow_id']}/runs", 'workflow_runs'):
        if not eligible(run, current):
            continue
        for artifact in list(pages(f"{base}/runs/{run['id']}/artifacts", 'artifacts')):
            if artifact['expired']:
                continue
            print(f"{'Would delete' if args.dry_run else 'Delete'} run {run['id']} artifact {artifact['name']} ({artifact['id']})", flush=True)
            if not args.dry_run:
                subprocess.run(['gh', 'api', '-X', 'DELETE',
                                f"{base}/artifacts/{artifact['id']}"], check=True)
            count += 1
    print(f'{count} artifacts selected; run history and failures retained')


if __name__ == '__main__':
    main()
