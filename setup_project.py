#!/usr/bin/env python3
"""Create a fresh portable sync project; never overwrite an account or ledger."""
import argparse
from datetime import date
import json
from pathlib import Path
import re


def initialize(project, athlete_id, start, end=None, sport='all', location='all', input_directory='apple_health_export'):
    project = Path(project).expanduser().resolve()
    if not re.fullmatch(r'[1-9][0-9]*', str(athlete_id)):
        raise ValueError('需要真实的数字 athlete ID')
    first = date.fromisoformat(start)
    if end is not None and date.fromisoformat(end) < first:
        raise ValueError('结束日期不能早于开始日期')
    if location not in ('all', 'indoor', 'outdoor') or not str(sport).strip() or not str(input_directory).strip():
        raise ValueError('运动类型、室内外或输入路径无效')
    target = project / 'sync_config.json'
    if target.exists() or (project / 'state/upload_history.sqlite3').exists():
        raise ValueError('已有配置或上传清单；请直接使用现有项目，不重新初始化')
    cfg = {'input_directory': str(input_directory), 'sport': sport, 'location': location,
           'start': first.isoformat(), 'end': end, 'expected_athlete_id': str(athlete_id), 'browser': 'auto'}
    project.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        json.dump(cfg, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    for folder in (str(Path(input_directory) / 'workout-routes'), 'state', 'exports'):
        (project / folder).mkdir(parents=True, exist_ok=True)
    return {'project': str(project), 'config': str(target), 'input_directory': str(project / input_directory)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'athlete-id', 'start'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--end')
    parser.add_argument('--sport', default='all')
    parser.add_argument('--location', choices=('all', 'indoor', 'outdoor'), default='all')
    parser.add_argument('--input-directory', default='apple_health_export')
    args = parser.parse_args()
    try:
        print(json.dumps(initialize(**vars(args)), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'错误：{exc}\n')


if __name__ == '__main__':
    main()
