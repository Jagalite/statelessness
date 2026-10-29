"""Chromium runtime gate for the installed zero-dependency browser graph.

Called inside the installed Kyoto job. Uses the same source-authored 36 cases,
with only Node CLI I/O replaced by a DOM result sink; no expected data rewritten.
"""
from pathlib import Path
import html
import re


def qualify(q, attempt, ts, fixture):
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if not chrome.is_file():
        raise RuntimeError('browser gate requires installed Google Chrome executable')
    attempt.watch('browser-executable', chrome)
    attempt.run('browser-version', [chrome, '--version'])
    source = (ts / 'build/consumer.js').read_text()
    source = re.sub(r"import \{\s*readFileSync\s*\} from ['\"]node:fs['\"];", '', source)
    source = source.replace("from '@jagalite/statelessness'", "from '../node_modules/@jagalite/statelessness/dist/index.js'")
    source = source.replace('const [mode, fixturePath] = process.argv.slice(2);', "const mode = 'observe', fixturePath = 'embedded';")
    source = source.replace("JSON.parse(readFileSync(fixturePath, 'utf8'))", q.canonical(fixture).decode().strip())
    source = source.replace("console.log(JSON.stringify({", "document.querySelector('pre').textContent = JSON.stringify({")
    source = source.replace("cases: results }));", "cases: results });")
    if 'readFileSync' in source or 'process.argv' in source or 'console.log' in source:
        raise ValueError('unexpected CLI source shape for browser adapter')
    browser = ts / 'build/browser.js'; browser.write_text(source)
    page = ts / 'browser.html'
    page.write_text('<!doctype html><pre>NOT_RUN</pre><script type="module" src="./build/browser.js"></script>')
    for path in (browser, page):
        attempt.register_generated(path, 'browser-adapter:' + path.name)
        attempt.watch('browser-adapter:' + path.name, path)
    raw, _, _ = attempt.run('browser-runtime', [chrome, '--headless=new', '--no-first-run',
        '--no-default-browser-check', '--disable-gpu', '--disable-background-networking',
        '--allow-file-access-from-files', '--user-data-dir=' + str(attempt.work / 'chrome-profile'),
        '--dump-dom', '--timeout=30000', '--virtual-time-budget=10000', page.as_uri()])
    match = re.search(rb'<pre>(.*?)</pre>', raw, re.S)
    if not match:
        raise ValueError('browser omitted DOM result')
    actual = q.strict_json(html.unescape(match[1].decode()))
    attempt.report['browser_runtime'] = dict(cases=q.validate_result(fixture, 'typescript', actual),
        host='Chromium headless macOS', artifact='installed npm tarball',
        limitations='finite shared text profile; not other browsers or mobile platforms')
