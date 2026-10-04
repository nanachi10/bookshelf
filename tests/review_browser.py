"""Isolated browser fixtures. Never opens a persistent/user browser profile."""
import argparse
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://localhost:18763/'
KEY = 'bookshelf:v3'
BASE_BOOK = dict(isbn='9784041010006', title='Fixture 1', series='Fixture',
                 vol=1, author='Author', publisher='Publisher', own='paper',
                 status='stock', added=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--revision', help='Serve index.html from this git revision without modifying the checkout')
    args = parser.parse_args()
    records = []
    source = subprocess.check_output(['git','show',args.revision+':index.html'],cwd=ROOT) if args.revision else None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=str(
            Path.home() / 'AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe'),
            args=['--disable-gpu'])

        def case(name, seed, check):
            ctx = browser.new_context(viewport=dict(width=390, height=844), service_workers='block')
            def route_request(route):
                if source and route.request.url in (URL, URL+'index.html'):
                    route.fulfill(body=source,content_type='text/html; charset=utf-8')
                elif route.request.url.startswith(URL):
                    route.continue_()
                else:
                    route.abort()
            ctx.route('**/*', route_request)
            ctx.add_init_script('const seed=' + json.dumps(seed) +
                                '; for(const [k,v] of Object.entries(seed)) localStorage.setItem(k,v);')
            page = ctx.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(URL)
            page.wait_for_timeout(100)
            try:
                evidence = check(page)
                records.append(dict(name=name, result='pass', evidence=evidence, page_errors=errors))
            except Exception as e:
                records.append(dict(name=name, result='fail', evidence=str(e), page_errors=errors))
            ctx.close()

        def empty(page):
            count = page.evaluate('books.length')
            assert count == 0, f'empty v3 resurrected {count} old book(s)'
            return dict(count=count)
        case('authoritative_empty_v3', {KEY:'[]', 'bookshelf:v2':json.dumps([BASE_BOOK])}, empty)

        def shape(page):
            assert page.locator('#stat').inner_text(), 'startup never rendered'
            assert page.evaluate('localStorage.getItem("bookshelf:v3")') == '{"unexpected":true}', 'raw changed'
            return 'invalid shape preserved and recovery notice rendered'
        case('invalid_root_shape', {KEY:'{"unexpected":true}'}, shape)

        def nullable(page):
            assert page.locator('#stat').inner_text(), 'startup never rendered'
            assert page.evaluate('localStorage.getItem("bookshelf:v3")') == '[null]', 'raw changed'
            return 'null record preserved and recovery notice rendered'
        case('null_record', {KEY:'[null]'}, nullable)

        def meta_shape(page):
            assert page.locator('#stat').inner_text(), 'startup never rendered'
            assert page.evaluate('books.length') == 1
            assert page.evaluate('localStorage.getItem("bookshelf:meta")') == '{"series":{"fixture|author":{"newVols":"2"}}}'
            return 'books render while invalid metadata is preserved'
        case('invalid_meta_shape', {KEY:json.dumps([BASE_BOOK]),
             'bookshelf:meta':'{"series":{"fixture|author":{"newVols":"2"}}}'},meta_shape)

        def invalid_volume(page):
            assert page.locator('#stat').inner_text(), 'startup never rendered'
            assert page.evaluate('books.length') == 0, 'invalid volume was accepted'
            assert page.evaluate('localStorage.getItem("bookshelf:v3:broken")')
            return 'invalid volume quarantined without overwriting the source'
        case('invalid_volume_shape', {KEY:json.dumps([dict(BASE_BOOK,vol=-1)])},invalid_volume)

        def volume(page):
            data = page.evaluate('({gaps:build(books)[0].gaps, vol:books[0].vol})')
            assert data['gaps'] == [], f'owned volume reported missing: {data}'
            assert data['vol'] == 1, data
            return data
        case('string_volume', {KEY:json.dumps([dict(BASE_BOOK, vol='1')])}, volume)

        def isbn_canonical(page):
            assert page.evaluate('books[0].isbn') == '9780306406157'
            return page.evaluate('books[0].isbn')
        case('isbn10_canonicalized', {KEY:json.dumps([dict(BASE_BOOK,isbn='0-306-40615-2')])},isbn_canonical)

        def import_atomic(page):
            page.click('#more')
            with page.expect_file_chooser() as chooser:
                page.click('#imp')
            chooser.value.set_files(dict(name='fixture.json', mimeType='application/json', buffer=json.dumps(
                dict(books=[dict(BASE_BOOK, isbn='Msecond', title='Second'),
                            dict(BASE_BOOK, isbn='Mbad', series={'bad':True})], done=['poison'], sort='title')
            ).encode()))
            page.wait_for_timeout(100)
            state = page.evaluate('({n:books.length, done:[...doneKeys],sort:sortBy, toast:document.getElementById("toast").textContent})')
            assert state['n'] == 1 and not state['done'] and state['sort'] == 'added', state
            return state
        case('invalid_import_is_atomic', {KEY:json.dumps([BASE_BOOK])}, import_atomic)

        def duplicate(page):
            state = page.evaluate('''async()=>{
              fetchByISBN=async isbn=>{await new Promise(r=>setTimeout(r,30));return {isbn,title:'Fixture'};};
              await Promise.all([add('9784041010006',{quiet:true}),add('9784041010006',{quiet:true})]);
              return books.length;
            }''')
            assert state == 1, f'concurrent add created {state} copies'
            return dict(count=state)
        case('concurrent_isbn_add', {KEY:'[]'}, duplicate)

        def preview_backend(page):
            state=page.evaluate('''async()=>{
              qdisk={fixture:{t:0,items:[]}};
              window.storage={set:async()=>{throw new Error('fixture storage unavailable')}};
              const result=await store.set('fixture:backend',[]);
              delete window.storage;
              return {result,local:localStorage.getItem('fixture:backend')};
            }''')
            assert state==dict(result=False,local=None), state
            return state
        case('preview_write_failure_keeps_backend', {},preview_backend)

        def export_dir(page):
            state = page.evaluate('''()=>{let captured;dl=text=>{captured=JSON.parse(text)};
                sortDir=-1;exportJSON();return captured;}''')
            assert state.get('dir') == -1, state
            return state
        case('backup_sort_direction', {KEY:json.dumps([BASE_BOOK])}, export_dir)

        def preserved(page):
            page.evaluate('async()=>{await addManual("Fixture", "paper");await save();}')
            state=page.evaluate('({raw:localStorage.getItem("bookshelf:v3"),backup:localStorage.getItem("bookshelf:v3:broken")})')
            assert state['raw']=='{broken', state
            return state
        case('broken_data_survives_later_save', {KEY:'{broken'}, preserved)

        def legacy(page):
            assert page.evaluate('books.length') == 1
            assert page.evaluate('books[0].vol') == 1
            return 'v2 migrated only when v3 is absent'
        case('legacy_migration', {'bookshelf:v2':json.dumps([BASE_BOOK])}, legacy)

        def roundtrip(page):
            page.click('#more')
            with page.expect_file_chooser() as chooser:
                page.click('#imp')
            chooser.value.set_files(dict(name='fixture.json',mimeType='application/json',buffer=json.dumps(
                dict(books=[BASE_BOOK],done=['fixture|author'],sort='title',dir=-1)).encode()))
            page.wait_for_timeout(100)
            if page.locator('#importcommit').count():
                page.click('#importcommit')
                page.wait_for_timeout(100)
            page.reload()
            page.wait_for_timeout(100)
            state=page.evaluate('({count:books.length,done:[...doneKeys],sort:sortBy,dir:sortDir})')
            assert state==dict(count=1,done=['fixture|author'],sort='title',dir=-1), state
            return state
        # Reload seeds are intentionally removed after the first document in this case by JS.
        # Use an empty seed so that saved data, rather than seed injection, is what reload reads.
        case('import_save_resume', {}, roundtrip)

        def escaped_identifier(page):
            page.locator('.book').click()
            assert not page.evaluate('window.__fixtureInjection'), 'ISBN injected an onclick attribute'
            return 'identifier stayed a data attribute'
        case('isbn_attribute_escaping', {KEY:json.dumps([dict(BASE_BOOK,
            isbn='M" onclick="window.__fixtureInjection=1',series=None,vol=None,title='Fixture')])},escaped_identifier)

        def large(page):
            state=page.evaluate('''async()=>{
                books=Array.from({length:2000},(_,i)=>normalize({isbn:'M'+i,title:'Fixture #'+i+' book',added:i}));
                const t=performance.now();await save();render();
                const elapsed=performance.now()-t;
                return {count:books.length,cards:document.querySelectorAll('.book').length,
                  ms:Math.round(elapsed), overflow:document.documentElement.scrollWidth>innerWidth};
            }''')
            # Initial paging keeps the whole shelf in storage while limiting mobile DOM work.
            assert state['count']==2000 and state['cards']==60 and not state['overflow'], state
            return state
        case('2000_books_mobile_layout_smoke', {},large)

        def new_error(page):
            state = page.evaluate('''async()=>{
              searchSeries=async()=>{throw new Error('HTTP 503')};
              const timer=window.setTimeout;window.setTimeout=(fn,ms,...args)=>timer(fn,0,...args);
              document.getElementById('more').click();await checkNew();
              window.setTimeout=timer;
              return document.getElementById('card').textContent;
            }''')
            assert '新しい巻は見つかりませんでした' not in state, state
            return state
        case('new_release_failure_is_not_no_results', {KEY:json.dumps([BASE_BOOK])}, new_error)

        def isolation(page):
            assert page.evaluate('books.length') == 0
            assert not page.evaluate('localStorage.getItem("bookshelf:v3:broken")')
            return 'fresh nonpersistent context; no user data'
        case('fixture_isolation', {}, isolation)
        browser.close()
    Path(args.output).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([dict(name=r['name'],result=r['result']) for r in records], ensure_ascii=True))
    return 1 if any(r['result']=='fail' for r in records) else 0


if __name__ == '__main__':
    raise SystemExit(main())
