"""Submit one PDF to MinerU's token-free cloud Agent API and save Markdown.

This command uploads the supplied PDF to MinerU. Requires requests and pypdf.
Use --task-id to resume polling without creating or uploading another task.
"""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from pypdf import PdfReader

BASE = 'https://mineru.net/api/v1/agent/parse'


def api_json(response):
    response.raise_for_status()
    value = response.json()
    if value.get('code') != 0:
        raise RuntimeError(f"MinerU error {value.get('code')}: {value.get('msg')}")
    return value


def https_url(value):
    if not isinstance(value, str) or urlparse(value).scheme != 'https':
        raise ValueError('Expected an HTTPS URL from MinerU')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task-id', help='Resume a previously uploaded task')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if not args.pdf.is_file():
        parser.error('PDF does not exist')
    if args.pdf.stat().st_size > 10 * 1024 * 1024:
        parser.error('Agent API supports at most 10 MB')
    pages = len(PdfReader(args.pdf).pages)
    if not 1 <= pages <= 20:
        parser.error('Agent API supports 1-20 pages')
    args.output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    # Do not inherit .netrc credentials; no Authorization header is required.
    session.trust_env = False
    start = time.monotonic()
    task_id = args.task_id
    request = {'file_name': args.pdf.name, 'language': 'en',
               'enable_table': True, 'enable_formula': True, 'is_ocr': False}
    if not task_id:
        if (args.output / 'task.json').exists():
            parser.error('Output already has a task; use --task-id to resume or a new output directory')
        created = api_json(session.post(BASE + '/file', json=request, timeout=60))
        task_id = created['data']['task_id']
        # Do not persist or print the signed upload URL.
        task = {'task_id': task_id, 'source': str(args.pdf.resolve()),
                'pages': pages, 'request': request, 'upload_completed': False}
        (args.output / 'task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Created task {task_id}', flush=True)
        with args.pdf.open('rb') as stream:
            upload = session.put(https_url(created['data']['file_url']), data=stream, timeout=120)
        if upload.status_code not in (200, 201):
            raise RuntimeError(f'Upload failed: HTTP {upload.status_code}; task saved, do not blindly resubmit')
        task['upload_completed'] = True
        (args.output / 'task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Uploaded PDF; waiting for parsing', flush=True)
    previous = None
    while time.monotonic() - start < args.timeout:
        response = session.get(BASE + '/' + task_id, timeout=60)
        if response.status_code == 429:
            print('Rate limited; waiting 30 seconds', flush=True)
            time.sleep(30)
            continue
        result = api_json(response)
        (args.output / 'status.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        data = result['data']
        state = data['state']
        if state != previous:
            print(f'{round(time.monotonic()-start, 1)}s: {state}', flush=True)
            previous = state
        if state == 'failed':
            raise RuntimeError(f"Parsing failed: {data.get('err_code')}: {data.get('err_msg')}")
        if state == 'done':
            markdown = session.get(https_url(data['markdown_url']), timeout=60)
            markdown.raise_for_status()
            if not markdown.content.strip():
                raise RuntimeError('Empty Markdown response')
            (args.output / 'paper.md').write_text(markdown.content.decode('utf-8-sig'), encoding='utf-8')
            (args.output / 'timing.json').write_text(json.dumps({'seconds': round(time.monotonic()-start, 2), 'resumed': bool(args.task_id)}), encoding='utf-8')
            print(f'Saved {args.output / "paper.md"}', flush=True)
            return
        time.sleep(8)
    raise TimeoutError(f'Polling timed out; resume with --task-id {task_id}')


if __name__ == '__main__':
    main()
