"""Two isolated browser stores, UI file exchange, rollback and stale-preview checks.
No user profile or cloud backend. Optional backup is read only, outside Git.
"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from review_browser import URL, KEY

META = 'bookshelf:meta'
A = dict(isbn='MfixtureA', title='Fixture A', own='paper', status='stock', note='Original', added=1)
B = dict(isbn='MfixtureB', title='Fixture B', own='paper', status='stock', added=2)
C = dict(isbn='MfixtureC', title='Fixture C', own='digital', status='read', added=3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--backup')
    args = parser.parse_args()
    output = Path(args.output)
    reports = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=str(Path.home() /
            'AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe'), args=['--disable-gpu'])

        def fresh(items=None, meta=None, width=390):
            ctx = browser.new_context(viewport=dict(width=width, height=844), service_workers='block')
            seed = {KEY: json.dumps(items if items is not None else [A, B]),
                    META: json.dumps(meta or dict(done=[], sort='added', dir=-1, series={}))}
            ctx.add_init_script("if(!localStorage.getItem('sync-seed')){const seed=" + json.dumps(seed) +
                ";for(const [k,v] of Object.entries(seed))localStorage.setItem(k,v);localStorage.setItem('sync-seed','1');}")
            ctx.route('**/*', lambda r: r.continue_() if r.request.url.startswith(URL) else r.abort())
            page = ctx.new_page()
            page.goto(URL)
            page.wait_for_function('document.getElementById("stat").textContent.length>0')
            return ctx, page

        def payload(items, done=None):
            data = dict(app='bookshelf', version=3, books=items)
            if done is not None:
                data['done'] = done
            return data

        def upload(page, data):
            page.locator('#more').click()
            page.locator('#devicesync').click()
            with page.expect_file_chooser() as fc:
                page.locator('#syncread').click()
            fc.value.set_files(dict(name='other-device.json', mimeType='application/json',
                                    buffer=json.dumps(data, ensure_ascii=False).encode()))
            page.wait_for_function('document.getElementById("syncapply") || document.getElementById("syncmessage")?.textContent')

        def state(page):
            return page.evaluate('({books, done:[...doneKeys],sort:sortBy,dir:sortDir})')

        def apply(page):
            page.locator('#synckeep').click()
            page.locator('#syncapply').click()
            page.wait_for_function('document.getElementById("syncfinishdownload") || !document.getElementById("syncapply")?.disabled')

        def case(name, fn, items=None):
            ctx, page = fresh(items)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            try:
                result = fn(page)
                assert not errors, errors
                reports.append(dict(name=name, result='pass', evidence=result))
            except Exception as e:
                reports.append(dict(name=name, result='fail', evidence=str(e), errors=errors))
            finally:
                ctx.close()

        def roundtrip(page):
            other_ctx, other = fresh([dict(A, status='read', readAt=1760000000000, note='Phone memo'), C], width=1280)
            try:
                page.evaluate("async()=>{await changeBooks(bs=>bs.map(b=>b.isbn==='MfixtureA'?{...b,note:'PC memo'}:b));render();}")
                upload(page, other.evaluate('shelfBackup()'))
                assert page.locator('#syncapply').is_disabled()
                assert state(page)['books'][0]['note'] == 'PC memo'
                page.locator('[data-choice="0:2"]').select_option('remote')
                page.locator('[data-choice="0:5"]').select_option('local')
                page.screenshot(path=str(output.parent / 'sync-mobile.png'))
                apply(page)
                assert page.locator('#syncfinishdownload').count() == 1
                now = state(page)
                assert len(now['books']) == 3 and now['dir'] == -1
                a = next(x for x in now['books'] if x['isbn'] == A['isbn'])
                assert a['status'] == 'read' and a['readAt'] == 1760000000000 and a['note'] == 'PC memo'
                previous = page.evaluate("JSON.parse(localStorage.getItem('bookshelf:before-sync'))")
                assert len(previous['books']) == 2 and previous['books'][0]['note'] == 'PC memo'
                with page.expect_download() as dl:
                    page.locator('#syncfinishdownload').click()
                data = json.loads(Path(dl.value.path()).read_text(encoding='utf-8'))
                upload(other, data)
                for select in other.locator('[data-choice]').all():
                    select.select_option('remote')
                apply(other)
                page.reload(); other.reload()
                page.wait_for_function('books.length===3'); other.wait_for_function('books.length===3')
                left = {b['isbn']: b for b in state(page)['books']}
                right = {b['isbn']: b for b in state(other)['books']}
                assert left == right, (left, right)
                return dict(devices=2, books=len(left), reopened=True, memo=a['note'], status=a['status'])
            finally:
                other_ctx.close()
        case('two_store_ui_exchange_merges_selected_fields_and_reopens', roundtrip)

        def no_delete(page):
            upload(page, payload([]))
            assert page.locator('[data-remove]:checked').count() == 0
            apply(page)
            assert len(state(page)['books']) == 2
            return 'An empty remote shelf never infers deletion'
        case('empty_remote_keeps_local_books', no_delete)

        def deletion(page):
            upload(page, payload([A]))
            page.locator('details:has([data-remove]) summary').click()
            page.locator('[data-remove="0"]').check()
            apply(page)
            assert [x['isbn'] for x in state(page)['books']] == [A['isbn']]
            assert len(page.evaluate("JSON.parse(localStorage.getItem('bookshelf:before-sync')).books")) == 2
            page.reload(); page.wait_for_function('books.length===1')
            return 'Only the explicitly selected missing book was deleted; backup retained'
        case('explicit_delete_and_recovery_backup', deletion)

        def flags(page):
            page.evaluate("async()=>{await changeMeta(m=>({...m,done:['old-completed']}));}")
            upload(page, payload([A, B], ['new-completed']))
            assert page.locator('#syncapply').is_disabled()
            for select in page.locator('[data-choice^="done:"]').all():
                select.select_option('remote')
            apply(page)
            assert state(page)['done'] == ['new-completed']
            assert state(page)['dir'] == -1
            return 'Completion added/removed explicitly; device sort retained'
        case('completion_flags_and_local_sort', flags)

        def legacy(page):
            upload(page, [dict(A, note='Old JSON memo'), B])
            page.locator('[data-choice="0:5"]').select_option('remote')
            apply(page)
            assert state(page)['books'][0]['note'] == 'Old JSON memo'
            return 'Legacy array backups can update an existing memo after review'
        case('legacy_array_support', legacy)

        for name, data in [
            ('duplicate_remote_ids_rejected', payload([A, A])),
            ('unsupported_version_rejected', dict(app='bookshelf', version=99, books=[])),
            ('foreign_app_rejected', dict(app='other', version=3, books=[])),
            ('bad_completion_rejected', payload([A], 'wrong')),
            ('invalid_book_rejected', payload([None]))
        ]:
            def invalid(page, data=data):
                before = state(page)
                upload(page, data)
                assert page.locator('#syncapply').count() == 0
                assert page.locator('#syncmessage').inner_text()
                assert state(page) == before
                return 'Rejected before any shelf mutation'
            case(name, invalid)

        def dup_local(page):
            upload(page, payload([A]))
            assert page.locator('#syncapply').count() == 0
            assert len(state(page)['books']) == 2
        case('duplicate_local_ids_do_not_silently_collapse', dup_local, [A, A])

        def quota(page, key):
            before = state(page)
            upload(page, payload([dict(A, note='Remote change'), B, C]))
            page.locator('[data-choice="0:5"]').select_option('remote')
            page.evaluate('''key=>{window.realSet=Storage.prototype.setItem;
              Storage.prototype.setItem=function(k,v){if(k===key)throw new DOMException('quota','QuotaExceededError');
              return realSet.call(this,k,v);};}''', key)
            apply(page)
            assert page.locator('#syncfinishdownload').count() == 0
            assert state(page) == before
            assert page.locator('#syncresult').inner_text()
            page.evaluate('()=>{Storage.prototype.setItem=realSet;}')
            page.reload(); page.wait_for_function('books.length===2')
            assert state(page) == before
            return 'Failure kept old shelf and restart recovered it'
        case('backup_quota_stops_before_mutation', lambda page: quota(page, 'bookshelf:before-sync'))
        case('books_quota_preserves_shelf', lambda page: quota(page, KEY))
        case('metadata_quota_rolls_back_books_and_recovers', lambda page: quota(page, META))

        def stale(page, external=False):
            upload(page, payload([dict(A, note='Remote change'), B]))
            page.locator('[data-choice="0:5"]').select_option('remote')
            if external:
                second = page.context.new_page(); second.goto(URL)
                second.wait_for_function('books.length===2')
                second.evaluate("async()=>{await changeBooks(bs=>bs.map(b=>({...b,note:'Other tab'})));}")
                second.close()
            else:
                page.evaluate("async()=>{await changeBooks(bs=>bs.map(b=>({...b,note:'Newer local edit'})));}")
            apply(page)
            assert page.locator('#syncfinishdownload').count() == 0
            assert '棚が変わりました' in page.locator('#syncresult').inner_text()
            expected = 'Other tab' if external else 'Newer local edit'
            assert page.evaluate("JSON.parse(localStorage.getItem('bookshelf:v3'))[0].note") == expected
            return 'Stale preview rejected without overwriting the newer edit'
        case('stale_local_preview_rejected', stale)
        case('another_tab_change_rejected', lambda page: stale(page, True))

        def ambiguity(page):
            upload(page, payload([dict(A, isbn='Msecond', title='Fixture series 1', series='Fixture series', vol=1)]))
            assert '保留1冊' in page.locator('#card').inner_text()
            apply(page)
            assert len(state(page)['books']) == 1
            return 'Ambiguous manual duplicate held for ISBN/edition review'
        case('manual_duplicate_held', ambiguity, [dict(A, series='Fixture series', vol=1)])

        def xss(page):
            attack = '<img src=x onerror="window.syncAttack=1">'
            upload(page, payload([dict(A, note=attack), B]))
            assert page.locator('.sync-values img').count() == 0
            assert page.evaluate('window.syncAttack||0') == 0
            assert attack in page.locator('.sync-values').inner_text()
            return 'Untrusted memo rendered as text'
        case('conflicting_text_is_escaped', xss)

        def fallback(page):
            page.locator('#more').click(); page.locator('#devicesync').click()
            page.evaluate("Object.defineProperty(navigator,'canShare',{value:()=>false,configurable:true})")
            with page.expect_download() as dl:
                page.locator('#syncshare').click()
            assert len(json.loads(Path(dl.value.path()).read_text(encoding='utf-8'))['books']) == 2
            return 'Unsupported file sharing falls back to a real JSON download'
        case('share_unsupported_download', fallback)

        def cancel(page):
            page.locator('#more').click(); page.locator('#devicesync').click()
            before = state(page)
            page.evaluate("""()=>{Object.defineProperty(navigator,'canShare',{value:()=>true,configurable:true});
              Object.defineProperty(navigator,'share',{value:()=>Promise.reject(new DOMException('cancel','AbortError')),configurable:true});}""")
            page.locator('#syncshare').click()
            assert '中止' in page.locator('#syncmessage').inner_text()
            assert state(page) == before
            return 'Cancellation neither changes shelf nor reports sync success'
        case('share_cancel_keeps_state', cancel)

        def repeat(page):
            upload(page, payload([A, B, C])); apply(page)
            original_backup = page.evaluate("localStorage.getItem('bookshelf:before-sync')")
            page.locator('#sheetx').click()
            upload(page, payload([A, B, C])); apply(page)
            assert len(state(page)['books']) == 3
            assert page.evaluate("localStorage.getItem('bookshelf:before-sync')") == original_backup
            return 'Repeated transfer does not add duplicates or overwrite the recovery backup'
        case('repeat_transfer_is_idempotent', repeat)

        def already_stale(page):
            second = page.context.new_page(); second.goto(URL)
            second.wait_for_function('books.length===2')
            second.evaluate("async()=>{await changeBooks(bs=>bs.map(b=>({...b,note:'New other tab edit'})));}")
            second.close()
            upload(page, payload([dict(A, note='Incoming'), B]))
            assert page.locator('#syncapply').count() == 0
            assert '別の画面で棚が変わっています' in page.locator('#syncmessage').inner_text()
            return 'An already stale tab cannot start a merge over a newer stored shelf'
        case('stale_before_preview_is_rejected', already_stale)

        if args.backup:
            data = json.loads(Path(args.backup).read_text(encoding='utf-8'))
            expected_count = len(data['books'])
            def copied_backup(page):
                before = state(page)
                upload(page, data)
                assert not page.locator('[data-choice]').count()
                assert f'同じ{expected_count}冊' in page.locator('#card').inner_text()
                apply(page)
                page.reload(); page.wait_for_function('books.length===' + str(expected_count))
                assert state(page)['books'] == before['books']
                return dict(books=expected_count, environment='isolated copy', original_untouched=True)
            case('pc_backup_copy_preserved_after_review_and_reload', copied_backup, data['books'])
        browser.close()
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([dict(name=r['name'], result=r['result']) for r in reports]))
    return int(any(r['result'] == 'fail' for r in reports))


if __name__ == '__main__':
    raise SystemExit(main())
