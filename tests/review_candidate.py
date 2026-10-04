"""Candidate checks with fixture storage, mocked fetch and a fresh browser context.
No user's browser, camera or external API is used. Slow/failing responses are fake.
"""
import argparse
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright
from review_browser import ROOT, URL, BASE_BOOK, KEY

META='bookshelf:meta'
PENDING='bookshelf:pending-import'
SNAPSHOT='''()=>({books,raw:localStorage.getItem('bookshelf:v3'),meta,done:[...doneKeys],
  sort:sortBy,dir:sortDir,sheet:sheetOpen,toast:document.getElementById('toast').textContent,
  logs:oplog.filter(x=>x.type!=='起動'),blocked:[...store.blocked]})'''
FAIL="""()=>{window.fixtureSet=Storage.prototype.setItem;
Storage.prototype.setItem=function(k,v){ if(k==='bookshelf:v3'||k==='bookshelf:meta')
throw new DOMException('fixture quota','QuotaExceededError'); return fixtureSet.call(this,k,v); };}"""
FAST="""()=>{window.fixtureTimer=window.setTimeout;
 window.setTimeout=(fn,ms,...args)=>fixtureTimer(fn,ms===30000?20:ms===1500?5:ms,...args);}"""
BOOK2=dict(BASE_BOOK,isbn='Mfixture2',title='Fixture 2',vol=2,added=2)
META0=dict(done=[],sort='added',dir=1,series={},newAt=0,exportAt=0,exportN=0)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--revision')
    parser.add_argument('--baseline-only',action='store_true')
    args=parser.parse_args()
    source=subprocess.check_output(['git','show',args.revision+':index.html'],cwd=ROOT) if args.revision else None
    reports=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=str(Path.home()/
            'AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe'),args=['--disable-gpu'])

        def case(name,fn,seed=None,init=None):
            baseline_cases={
                'manual_add_failure_does_not_enter_shelf','isbn_add_failure_does_not_claim_success',
                'remove_many_failure_keeps_books','bulk_read_failure_keeps_status_and_readAt',
                'single_delete_failure_keeps_editor','undo_failure_does_not_restore_in_memory_only',
                'undo_skips_isbn_readded_after_delete','completed_flag_failure_is_rolled_back',
                'async_backend_concurrent_adds_do_not_lose_books','cover_save_failure_keeps_books',
                'response_body_has_deadline','older_response_does_not_overwrite_new_cache',
                'older_search_never_replaces_latest_results','short_input_cancels_stale_suggestion',
                'selected_novel_excludes_comic_volumes','ambiguous_edition_is_not_saved_as_miss',
                'new_release_reuses_one_fresh_lookup_for_two_editions','large_shelf_pages_without_losing_books_or_nodes'}
            if args.baseline_only and name not in baseline_cases:
                return
            context=browser.new_context(service_workers='block',viewport=dict(width=390,height=844))
            seed=seed or {KEY:json.dumps([BASE_BOOK]),META:json.dumps(META0)}
            context.add_init_script('''if(!localStorage.getItem('fixture-seeded')){
                const seed='''+json.dumps(seed)+''';for(const [k,v] of Object.entries(seed))localStorage.setItem(k,v);
                localStorage.setItem('fixture-seeded','1');}''')
            if init:
                context.add_init_script(init)
            def route(r):
                if source and r.request.url in [URL,URL+'index.html']:
                    r.fulfill(body=source,content_type='text/html; charset=utf-8')
                elif r.request.url.startswith(URL):
                    r.continue_()
                else:
                    r.abort()
            context.route('**/*',route)
            page=context.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(URL);page.wait_for_function('document.getElementById("stat").textContent.length>0')
                evidence=fn(page)
                assert not errors,errors
                reports.append(dict(name=name,result='pass',evidence=evidence,page_errors=errors))
            except Exception as e:
                reports.append(dict(name=name,result='fail',evidence=str(e),page_errors=errors))
            context.close()

        def safe_failure(page,action):
            before=page.evaluate(SNAPSHOT)
            page.evaluate(FAIL)
            result=page.evaluate(action)
            state=page.evaluate(SNAPSHOT)
            assert state['books']==before['books'] and state['raw']==before['raw'],state
            assert state['meta']==before['meta'] and state['done']==before['done'],state
            assert state['logs']==before['logs'],state
            assert '保存できません' in state['toast'],state
            return dict(result=result,unchanged=True,toast=state['toast'])

        case('manual_add_failure_does_not_enter_shelf',lambda p:safe_failure(p,
            "async()=>!!await addManual('Fixture new','paper')"))
        case('isbn_add_failure_does_not_claim_success',lambda p:safe_failure(p,
            "async()=>!!await add('Mnew',{rec:{isbn:'Mnew',title:'Fixture new'},quiet:true})"))
        case('remove_many_failure_keeps_books',lambda p:safe_failure(p,
            "async()=>await removeMany(books.slice(),'Fixture')"))
        case('bulk_read_failure_keeps_status_and_readAt',lambda p:safe_failure(p,
            "async()=>{await bulkApply(build(books)[0],1,b=>setStatus(b,'read'),'読書状態');}"))

        def delete_ui(page):
            page.evaluate('openBook(books[0].isbn)')
            return safe_failure(page,"async()=>{await document.getElementById('del').onclick();return sheetOpen;}")
        case('single_delete_failure_keeps_editor',delete_ui)

        def undo_failure(page):
            page.evaluate("async()=>{await removeMany(books.slice(),'Fixture');window.fixtureUndo=document.querySelector('#toast button').onclick;}")
            result=safe_failure(page,"async()=>{fixtureUndo();await stateQueue;}")
            assert page.evaluate('books.length')==0
            return result
        case('undo_failure_does_not_restore_in_memory_only',undo_failure)

        def undo_duplicate(page):
            state=page.evaluate('''async()=>{
              const b=books[0];await removeMany([b],'Fixture');const undo=document.querySelector('#toast button').onclick;
              await add(b.isbn,{rec:b,quiet:true});undo();await stateQueue;
              return {count:books.filter(x=>x.isbn===b.isbn).length,raw:JSON.parse(localStorage.getItem(KEY)).length};
            }''')
            assert state==dict(count=1,raw=1),state
            return state
        case('undo_skips_isbn_readded_after_delete',undo_duplicate)

        case('completed_flag_failure_is_rolled_back',lambda p:safe_failure(p,
            "async()=>await toggleDone(build(books)[0])"))

        def async_adds(page):
            state=page.evaluate('''async()=>{
              let writes=0;window.storage={get:async k=>({value:localStorage.getItem(k)}),set:async(k,v)=>{
                const delay=k===KEY&&++writes===1?40:5;await new Promise(r=>setTimeout(r,delay));localStorage.setItem(k,v);}};
              await Promise.all([add('MA',{rec:{isbn:'MA',title:'A'},quiet:true}),add('MB',{rec:{isbn:'MB',title:'B'},quiet:true})]);
              return {memory:books.map(b=>b.isbn).sort(),saved:JSON.parse(localStorage.getItem(KEY)).map(b=>b.isbn).sort()};
            }''')
            assert state['memory']==state['saved'] and len(state['saved'])==3,state
            return state
        case('async_backend_concurrent_adds_do_not_lose_books',async_adds)

        def load_preview(page,data):
            page.click('#more')
            with page.expect_file_chooser() as chooser:
                page.click('#imp')
            chooser.value.set_files(dict(name='fixture.json',mimeType='application/json',
                buffer=json.dumps(data,ensure_ascii=False).encode('utf-8')))
            page.wait_for_selector('#importcommit')

        def import_preview(page):
            load_preview(page,dict(books=[BASE_BOOK,dict(BOOK2,isbn='Mphoto-2'),
                dict(BASE_BOOK,isbn='Mphoto-overlap')],pending=[dict(title='Uncertain',reason='巻未確認')]))
            assert page.evaluate('books.length')==1
            text=page.locator('#card').inner_text()
            assert '新しく入る1冊' in text and '同じISBN1件' in text and '2件は保留します' in text,text
            page.click('#importcommit');page.wait_for_function('books.length===2')
            state=page.evaluate('({ids:books.map(b=>b.isbn),note:books.find(b=>b.isbn=== "9784041010006").note})')
            assert 'Mphoto-overlap' not in state['ids'] and state['note']=='',state
            page.reload();page.wait_for_function('books.length===2')
            return state
        case('photo_preview_duplicate_and_ambiguity_hold',import_preview)

        def import_meta_failure(page):
            load_preview(page,dict(books=[BOOK2],done=['fixture|author'],sort='title',dir=-1))
            before=page.evaluate(SNAPSHOT)
            page.evaluate('''()=>{window.fixtureSet=Storage.prototype.setItem;window.fixtureFailed=false;
              Storage.prototype.setItem=function(k,v){if(k==='bookshelf:meta'&&!fixtureFailed){fixtureFailed=true;
              throw new DOMException('fixture second key failure','QuotaExceededError');}return fixtureSet.call(this,k,v);};}''')
            page.click('#importcommit');page.wait_for_timeout(80)
            state=page.evaluate(SNAPSHOT)
            assert state['books']==before['books'] and state['raw']==before['raw'],state
            assert state['meta']==before['meta'] and state['done']==before['done'],state
            assert state['sort']==before['sort'] and state['dir']==before['dir'] and state['sheet'],state
            assert page.evaluate('JSON.parse(localStorage.getItem("bookshelf:pending-import"))') is None
            page.evaluate('()=>{Storage.prototype.setItem=window.fixtureSet;}')
            page.click('#importcommit');page.wait_for_function('books.length===2')
            return 'second key failed, old book/meta restored; same preview retry committed both'
        case('import_second_key_failure_rolls_back_and_retry',import_meta_failure)

        def import_rollback_failure(page):
            page.evaluate('''()=>{window.fixtureSet=Storage.prototype.setItem;window.fixtureWritten=false;
              Storage.prototype.setItem=function(k,v){
                if(k==='bookshelf:v3'&&!fixtureWritten){fixtureWritten=true;return fixtureSet.call(this,k,v);}
                if(k==='bookshelf:v3'||k==='bookshelf:meta')throw new DOMException('fixture failure','QuotaExceededError');
                return fixtureSet.call(this,k,v);};}''')
            state=page.evaluate('''async b=>{await importState([normalize(b)],{books:[b],sort:'title'});
              return {count:books.length,blocked:[...store.blocked],pending:!!JSON.parse(localStorage.getItem(PENDING))};}''',BOOK2)
            assert state['count']==1 and KEY in state['blocked'] and META in state['blocked'] and state['pending'],state
            page.evaluate('()=>{Storage.prototype.setItem=window.fixtureSet;}')
            page.reload();page.wait_for_function('document.getElementById("stat").textContent.length>0')
            assert page.evaluate('books.length')==1
            assert page.evaluate('JSON.parse(localStorage.getItem(PENDING))') is None
            assert page.evaluate('store.blocked.size')==0
            return state
        case('import_rollback_failure_freezes_then_boot_recovers',import_rollback_failure)

        interrupted={KEY:json.dumps([BASE_BOOK,BOOK2]),META:json.dumps(dict(META0,sort='title')),
                     PENDING:json.dumps(dict(version=1,before=dict(books=[BASE_BOOK],meta=META0)))}
        def recovered(page):
            state=page.evaluate(SNAPSHOT)
            assert len(state['books'])==1 and state['sort']=='added',state
            assert len(json.loads(state['raw']))==1 and not state['blocked'],state
            assert page.evaluate('JSON.parse(localStorage.getItem(PENDING))') is None
            return 'interrupted two-key import restored old shelf/settings on startup'
        case('startup_recovers_interrupted_import',recovered,interrupted)

        def frozen(page):
            state=page.evaluate(SNAPSHOT)
            assert len(state['books'])==1 and KEY in state['blocked'] and META in state['blocked'],state
            assert len(json.loads(state['raw']))==2,state
            return 'safe old snapshot displayed while write failure leaves journal and blocks changes'
        case('startup_failed_recovery_displays_safe_snapshot',frozen,interrupted,
             "const fixtureSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='bookshelf:v3'||k==='bookshelf:meta')throw new DOMException('fixture','QuotaExceededError');return fixtureSet.call(this,k,v);};")

        def recover_export(page):
            page.evaluate('''()=>{window.fixtureExport=null;dl=(text,ext)=>{fixtureExport={text:JSON.parse(text),ext};};}''')
            page.click('#more');page.click('#recoverydl');page.wait_for_function('fixtureExport!==null')
            state=page.evaluate('fixtureExport')
            assert state['text']['app']=='bookshelf-recovery' and state['text']['raw'][KEY]=='{broken',state
            assert page.evaluate('localStorage.getItem(KEY)')=='{broken'
            return dict(raw_preserved=True,export_type=state['ext'])
        case('recovery_export_preserves_corrupt_raw',recover_export,{KEY:'{broken'})

        def covers_failure(page):
            page.evaluate("()=>{window.fetch=async()=>({ok:true,status:200,json:async()=>[{summary:{cover:'https://fixture.invalid/cover'}}]});}")
            return safe_failure(page,"async()=>{await fillCoversFor(books.slice());}")
        case('cover_save_failure_keeps_books',covers_failure)

        def stale_cover(page):
            state=page.evaluate('''async()=>{
              window.fetch=()=>new Promise(r=>window.fixtureResolve=()=>r({ok:true,status:200,json:async()=>[{summary:{cover:'https://fixture.invalid/old'}}]}));
              const job=fillCoversFor(books.slice());await Promise.resolve();
              await changeBooks(current=>current.map(b=>Object.assign({},b,{cover:'https://fixture.invalid/manual'})));
              fixtureResolve();await job;
              return {cover:books[0].cover,saved:JSON.parse(localStorage.getItem(KEY))[0].cover};
            }''')
            assert state['cover']==state['saved']=='https://fixture.invalid/manual',state
            return state
        case('late_cover_does_not_override_manual_cover',stale_cover)

        def body_timeout(page):
            page.evaluate(FAST)
            state=page.evaluate('''async()=>{
              window.fetch=async()=>({ok:true,status:200,text:()=>new Promise(()=>{})});
              return await Promise.race([ndlFetch('https://fixture.invalid/slow').then(()=>'resolved',e=>e.name),
                new Promise(r=>fixtureTimer(()=>r('still pending'),150))]);
            }''')
            assert state=='TimeoutError',state
            return state
        case('response_body_has_deadline',body_timeout)

        def retry(page):
            page.evaluate(FAST)
            state=page.evaluate('''async()=>{
              let calls=0;window.fetch=async()=>++calls===1?{ok:false,status:429,headers:{get:()=>null}}:
                {ok:true,status:200,text:async()=>'<rss><channel></channel></rss>'};
              const result=await ndlQuery({title:'Fixture retry',fresh:true});return {calls,count:result.length};
            }''')
            assert state==dict(calls=2,count=0),state
            return state
        case('429_retries_once_with_gap',retry)

        def cancel_retry(page):
            state=page.evaluate('''async()=>{
              let calls=0;window.fetch=async()=>{calls++;return {ok:false,status:429,headers:{get:()=>null}}};
              const ctl=new AbortController(),job=ndlFetch('https://fixture.invalid/retry',ctl.signal).catch(e=>e.name);
              await new Promise(r=>setTimeout(r,10));ctl.abort();return {calls,error:await job};
            }''')
            assert state==dict(calls=1,error='AbortError'),state
            return state
        case('cancel_during_retry_wait_stops_second_request',cancel_retry)

        def fallback(page):
            page.evaluate(FAST)
            state=page.evaluate('''async()=>{
              const calls=[];window.fetch=(url,opts)=>{calls.push(url);return url.includes('opensearch')?
                new Promise(()=>{}):Promise.resolve({ok:true,status:200,json:async()=>[{summary:{title:'Fixture fallback'}}]});};
              const b=await fetchByISBN('9784101010014');return {title:b&&b.title,calls:calls.length};
            }''')
            assert state==dict(title='Fixture fallback',calls=2),state
            return state
        case('isbn_timeout_uses_existing_openbd_fallback',fallback)

        def stale_cache(page):
            state=page.evaluate('''async()=>{
              const jobs=[];ndlFetch=()=>new Promise(r=>jobs.push(r));
              const a=ndlQuery({title:'same',fresh:true});await new Promise(r=>setTimeout(r,0));
              const b=ndlQuery({title:'same',fresh:true});await new Promise(r=>setTimeout(r,0));
              const newer=[{isbn:'Mnew',title:'new'}],older=[{isbn:'Mold',title:'old'}];
              jobs[1](newer);await b;jobs[0](older);await a;qcache.clear();
              return (await ndlQuery({title:'same'}))[0].title;
            }''')
            assert state=='new',state
            return state
        case('older_response_does_not_overwrite_new_cache',stale_cache)

        def canceled_cache(page):
            state=page.evaluate('''async()=>{
              const ctl=new AbortController();window.fetch=()=>new Promise(r=>window.fixtureResolve=r);
              const p=ndlQuery({title:'cancel',cnt:200,signal:ctl.signal}).catch(e=>e.name);
              await new Promise(r=>setTimeout(r,0));ctl.abort();const error=await p;
              fixtureResolve({ok:true,status:200,text:async()=>'<rss><channel/></rss>'});await Promise.resolve();
              return {error,cache:qcache.size,disk:Object.keys(qdisk||{}).length};
            }''')
            assert state==dict(error='AbortError',cache=0,disk=0),state
            return state
        case('canceled_request_not_cached_or_fallbacked',canceled_cache)

        def search_superseded(page):
            page.evaluate('''()=>{window.fixtureSearch=[];searchBooks=(q,signal)=>new Promise(resolve=>fixtureSearch.push({q,signal,resolve}));openFind();}''')
            page.fill('#fq','old');page.click('#fgo');page.wait_for_function('fixtureSearch.length===1')
            page.fill('#fq','new');page.click('#fgo');page.wait_for_function('fixtureSearch.length===2')
            page.evaluate("()=>{fixtureSearch[1].resolve([{isbn:'Mnew',title:'new result'}]);}")
            page.wait_for_function("document.getElementById('fres').textContent.includes('new result')")
            page.evaluate("()=>{fixtureSearch[0].resolve([{isbn:'Mold',title:'old result'}]);}")
            page.wait_for_timeout(20)
            text=page.locator('#fres').inner_text()
            assert 'new result' in text and 'old result' not in text,text
            assert page.evaluate('fixtureSearch[0].signal.aborted')
            return text
        case('older_search_never_replaces_latest_results',search_superseded)

        def suggest_short(page):
            page.evaluate('''()=>{window.fixtureSuggest=[];ndlQuery=p=>new Promise(r=>fixtureSuggest.push({p,r}));openFind();}''')
            page.fill('#fq','long');page.wait_for_function('fixtureSuggest.length===1')
            page.fill('#fq','x')
            page.evaluate("()=>{fixtureSuggest[0].r([{isbn:'Mold',title:'Old suggestion'}]);}")
            page.wait_for_timeout(20)
            assert 'Old suggestion' not in page.locator('#fpre').inner_text()
            return 'editing to one character invalidates and aborts pending suggestions'
        case('short_input_cancels_stale_suggestion',suggest_short)

        def leave_search(page):
            page.evaluate('''()=>{window.fixtureSignal=null;searchBooks=(q,signal)=>{fixtureSignal=signal;return new Promise(()=>{})};openFind('Fixture');}''')
            page.evaluate("()=>{openBulkInput();}")
            assert page.evaluate('fixtureSignal.aborted')
            assert 'まとめて入力' in page.locator('#card').inner_text()
            return 'navigation canceled pending network; new screen remains'
        case('leaving_screen_cancels_request',leave_search)

        eds=[dict(ak='author',pk='novel',author='Author',publisher='Novel',items=[dict(BASE_BOOK,publisher='Novel')]),
             dict(ak='artist',pk='comic',author='Artist',publisher='Comic',items=[dict(BOOK2,author='Artist',publisher='Comic')])]
        def edition_only(page):
            state=page.evaluate('''async eds=>{searchSeriesAll=async()=>eds;
              return (await searchSeries('Fixture','author|novel',true)).map(b=>b.isbn);}''',eds)
            assert state==[BASE_BOOK['isbn']],state
            return state
        case('selected_novel_excludes_comic_volumes',edition_only)

        def edition_ambiguous(page):
            state=page.evaluate('''async eds=>{searchSeriesAll=async()=>eds;openWith('<h2>Fixture</h2>');
              await checkNew(true);return {series:meta.series,text:document.getElementById('card').textContent,
              books:books.map(b=>b.isbn)};}''',[dict(e,ak='other'+str(i),items=[dict(b,isbn='Mother'+str(i)) for b in e['items']]) for i,e in enumerate(eds)])
            assert state['series']=={} and '版を特定できません' in state['text'] and '別の版' in state['text'],state
            assert state['books']==[BASE_BOOK['isbn']],state
            return state
        case('ambiguous_edition_is_not_saved_as_miss',edition_ambiguous)

        def edition_isbn_evidence(page):
            state=page.evaluate('''async eds=>{searchSeriesAll=async()=>eds;
              return (await searchSeries('Fixture','author|publisher',true)).map(b=>b.isbn);}''',eds)
            assert state==[BASE_BOOK['isbn']],state
            return 'ISBN evidence matched old shelf despite publisher grouping change; no shelf migration'
        case('stored_isbn_can_resolve_changed_edition_key',edition_isbn_evidence)

        def bounded_bulk(page):
            state=page.evaluate('''async()=>{
              let active=0,max=0;searchSeriesAll=async name=>{
                active++;max=Math.max(max,active);await new Promise(r=>setTimeout(r,25));active--;
                return [{ak:'author',pk:'',author:'Author',count:1,items:[{isbn:'M'+name,title:name+' 1',series:name,vol:1}]}];};
              openBulkInput();document.getElementById('blines').value=['A 1','B 1','C 1','D 1'].join(String.fromCharCode(10));
              await runBulkInput();return {max,names:bulkFound.map(r=>r.name),enabled:!document.getElementById('bcommit').disabled};
            }''')
            assert 1<=state['max']<=2 and state['names']==['A','B','C','D'] and state['enabled'],state
            page.evaluate(FAIL);page.click('#bcommit');page.wait_for_timeout(40)
            assert page.evaluate('books.length')==1
            assert page.locator('#bcommit').is_enabled()
            return state
        case('bulk_lookup_is_bounded_ordered_and_commit_failure_retryable',bounded_bulk)

        def cache_share_new(page):
            state=page.evaluate('''async()=>{
              const b=Object.assign({},books[0],{isbn:'Mcomic',author:'Artist'});
              books=[...books,b];buildCache=null;let calls=0;
              searchSeriesAll=async()=>{calls++;return [
                {ak:'author',pk:'',items:[books[0]]},{ak:'artist',pk:'',items:[b]}];};
              openWith('<h2>New</h2>');await checkNew(true);return {calls,records:Object.keys(meta.series).length};
            }''')
            assert state==dict(calls=1,records=2),state
            return state
        case('new_release_reuses_one_fresh_lookup_for_two_editions',cache_share_new)

        def undo_latest(page):
            state=page.evaluate('''async()=>{
              const old=books[0];await changeBooks(current=>current.map(b=>Object.assign({},b,{note:'Latest saved note'})));
              await removeMany([old],'Fixture');document.querySelector('#toast button').click();await stateQueue;
              return {note:books[0].note,saved:JSON.parse(localStorage.getItem(KEY))[0].note};
            }''')
            assert state==dict(note='Latest saved note',saved='Latest saved note'),state
            return state
        case('undo_restores_latest_deleted_record_not_stale_reference',undo_latest)

        def cancel_new(page):
            page.evaluate('''()=>{window.fixtureSignal=null;searchSeriesAll=(name,fresh,signal)=>{
              fixtureSignal=signal;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)));};
              openWith('<h2>New</h2>');checkNew(true);}''')
            page.wait_for_function('fixtureSignal!==null')
            page.click('#cncancel');page.wait_for_timeout(20)
            assert page.evaluate('fixtureSignal.aborted') and page.evaluate('Object.keys(meta.series).length')==0
            assert '中止しました' in page.locator('#cnprog').inner_text()
            return 'visible cancel aborts new-release request without saving a miss'
        case('new_release_cancel_button_preserves_state',cancel_new)

        def cancel_bulk(page):
            page.evaluate('''()=>{window.fixtureSignal=null;searchSeriesAll=(name,fresh,signal)=>{
              fixtureSignal=signal;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)));};
              openBulkInput();document.getElementById('blines').value='A 1';runBulkInput();}''')
            page.wait_for_function('fixtureSignal!==null')
            page.click('#bcancel');page.wait_for_timeout(20)
            assert page.evaluate('fixtureSignal.aborted')
            assert page.locator('#bgo').is_enabled() and page.locator('#bcommit').is_disabled()
            assert page.locator('#blines').input_value()=='A 1' and page.evaluate('books.length')==1
            return 'visible cancel leaves input and prevents unconfirmed batch commit'
        case('bulk_cancel_leaves_input_and_disables_unconfirmed_commit',cancel_bulk)

        def cover_image_failure(page):
            before=page.evaluate('JSON.stringify(books)')
            page.evaluate('''()=>{const image=document.createElement('img');image.dataset.b=books[0].isbn;
              image.dataset.original=books[0].cover;image.src='https://fixture.invalid/good';
              Object.defineProperty(image,'naturalWidth',{value:2});Object.defineProperty(image,'naturalHeight',{value:2});
              ckCover(image);}''')
            assert page.evaluate('JSON.stringify(books)')==before
            page.evaluate(FAIL);page.wait_for_timeout(1250)
            assert page.evaluate('JSON.stringify(books)')==before
            return 'image success does not mutate shelf before its deferred save; quota failure preserves old cover'
        case('image_onload_deferred_save_failure_preserves_books',cover_image_failure)

        def paging(page):
            state=page.evaluate('''()=>{
              books=Array.from({length:2000},(_,i)=>normalize({isbn:'M'+i,title:'Single '+i+'x',own:'paper'}));
              buildCache=null;shelfLimit=60;render();window.fixtureFirst=document.querySelector('.book');
              return {cards:document.querySelectorAll('.book').length,stat:document.getElementById('stat').textContent};
            }''')
            assert state['cards']==60 and '登録2000' in state['stat'],state
            page.click('#morecards')
            assert page.locator('.book').count()==120
            assert page.evaluate('fixtureFirst===document.querySelector(".book")')
            page.evaluate('()=>{shelfLimit=2000;render();}')
            assert page.locator('.book').count()==2000
            return dict(first=60,next=120,all=2000,first_node_preserved=True)
        case('large_shelf_pages_without_losing_books_or_nodes',paging)
        browser.close()
    Path(args.output).write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([dict(name=x['name'],result=x['result']) for x in reports]))
    return 1 if any(x['result']=='fail' for x in reports) else 0


if __name__=='__main__':
    raise SystemExit(main())
