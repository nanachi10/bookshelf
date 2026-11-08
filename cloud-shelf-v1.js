/* Private repository is the canonical shelf. Each device keeps a last-synced
   copy for merging offline edits; GitHub's blob SHA protects concurrent PUTs. */
const shelfRepo='https://api.github.com/repos/nanachi10/bookshelf-private-backup';
const cloudTokenKey='bookshelf:cloud:access';
const cloudBaseKey='bookshelf:cloud:base';
let shelfChangedInOtherTab=false;
let cloudReady=false,cloudBusy=false,cloudTimer,cloudChoices={},cloudConflicts=[],cloudMessage='';
const cloudEq=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
function cloudStatus(text){
  cloudMessage=text;
  document.getElementById('cloudstatus').textContent=text;
  const el=document.getElementById('cloudmessage');
  if(el) el.textContent=text;
}
function scheduleShelfSync(){
  if(!cloudReady||cloudBusy||!localStorage.getItem(cloudTokenKey)) return;
  clearTimeout(cloudTimer);
  cloudTimer=setTimeout(runShelfSync,1300);
}
async function cloudRequest(path,token,options={}){
  const response=await fetch(shelfRepo+path,{...options,cache:'no-store',redirect:'error',
    signal:AbortSignal.timeout(18000),headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'}});
  if(!response.ok){
    const err=new Error(response.status===401||response.status===403||response.status===404
      ?'同期先のアクセス権を確認してください':'同期を再試行します（HTTP '+response.status+'）');
    err.status=response.status;throw err;
  }
  return response.json();
}
function cloudData(raw){
  if(raw.app!=='bookshelf'||!Array.isArray(raw.books)) throw Error('同期先の棚の形式を確認してください');
  const list=raw.books.map(normalize);
  if(new Set(list.map(b=>b.isbn)).size!==list.length) throw Error('同期先で本のIDが重複しています');
  if(raw.done!=null&&(!Array.isArray(raw.done)||!raw.done.every(k=>typeof k==='string'))) throw Error('完結記録が不正です');
  return {books:list,done:[...new Set(raw.done||[])].sort()};
}
function cloudMerge(base,local,remote){
  const conflicts=[];
  function choose(id,label,a,l,r){
    if(cloudEq(l,r)||cloudEq(r,a)) return l;
    if(cloudEq(l,a)) return r;
    const signature=JSON.stringify([id,a,l,r]);
    if(Object.hasOwn(cloudChoices,signature)) return cloudChoices[signature]==='local'?l:r;
    conflicts.push({signature,label,local:l,remote:r});return r;
  }
  const maps=[base,local,remote].map(set=>new Map(set.books.map(b=>[b.isbn,b])));
  const merged=[];
  for(const id of new Set([...maps[2].keys(),...maps[1].keys(),...maps[0].keys()])){
    const [a,l,r]=maps.map(m=>m.get(id));
    const name=(l||r||a).title;
    let book;
    if(l&&r){
      book={};
      for(const field of new Set([...Object.keys(l),...Object.keys(r)])){
        // Cover discovery runs independently on both devices. A usable URL wins.
        book[field]=field==='cover'&&!cloudEq(a?.cover,l.cover)&&!cloudEq(a?.cover,r.cover)?(r.cover||l.cover||''):choose([id,field],name+' · '+field,a?.[field],l[field],r[field]);
      }
    }else book=choose(id,name,a,l,r);
    if(book) merged.push(book);
  }
  const done=[...new Set([...base.done,...local.done,...remote.done])].filter(k=>
    choose(k,k,base.done.includes(k),local.done.includes(k),remote.done.includes(k))).sort();
  return {data:{books:merged,done},conflicts};
}
async function syncOneShelf(token){
  const repo=await cloudRequest('',token);
  if(repo.private!==true) throw Error('同期先の非公開設定を確認してください');
  const path='/contents/live-shelf.json';
  const file=await cloudRequest(path+'?ref=main',token);
  const baseRecord=JSON.parse(localStorage.getItem(cloudBaseKey)||'null');
  let remote;
  if(baseRecord?.sha===file.sha) remote=baseRecord.data;
  else{
    const blob=file.content?file:await cloudRequest('/git/blobs/'+file.sha,token);
    const bytes=Uint8Array.from(atob(blob.content),c=>c.charCodeAt(0));
    remote=cloudData(JSON.parse(new TextDecoder().decode(bytes)));
  }
  await stateJob(async()=>{
    if(shelfChangedInOtherTab) throw Error('別タブで更新されました。再読み込みしてください');
    if(store.bad||store.blocked.size) throw Error('保存データの復旧が必要です');
    const disk=await store.get(KEY);
    if(!cloudEq((disk||[]).map(normalize),books.map(normalize))) throw Error('他のタブで変更されました。再読み込みしてください');
    const local=cloudData(shelfBackup());
    const {data,conflicts}=cloudMerge(baseRecord?.data||{books:[],done:[]},local,remote);
    cloudConflicts=conflicts;
    if(conflicts.length){cloudStatus('同期の違いを確認');return;}
    let sha=file.sha;
    if(!cloudEq(data,remote)){
      const raw=new TextEncoder().encode(JSON.stringify({app:'bookshelf',version:3,...data}));
      let binary=''; for(const byte of raw) binary+=String.fromCharCode(byte);
      const put=await cloudRequest(path,token,{method:'PUT',body:JSON.stringify({message:'本棚を同期',branch:'main',sha,content:btoa(binary)})});
      sha=put.content.sha;
    }
    if(!cloudEq(data,local)){
      if(!await writeShelfState(data.books,{...metaSnapshot(),done:data.done})) throw Error('この端末に保存できませんでした');
      render();
    }
    if(baseRecord?.sha!==sha) localStorage.setItem(cloudBaseKey,JSON.stringify({sha,data}));
    cloudChoices={};cloudStatus('同期済み');
  });
}
async function runShelfSync(){
  if(!cloudReady||cloudBusy) return;
  const token=localStorage.getItem(cloudTokenKey);
  if(!token) return;
  cloudBusy=true;
  try{
    if(!navigator.onLine) throw Error('オフライン · 同期待ち');
    const job=()=>syncOneShelf(token);
    if(navigator.locks) await navigator.locks.request('bookshelf:cloud',job);
    else await job();
  }catch(e){
    cloudStatus(e.status===409||e.status===422?'他の端末の更新を再取得します':e.message);
  }finally{cloudBusy=false;}
}
function initCloudShelf(){
  cloudReady=true;
  window.addEventListener('storage',event=>{
    if(event.key!==KEY||!localStorage.getItem(cloudTokenKey)) return;
    shelfChangedInOtherTab=true;
    if(!sheetOpen) location.reload();
    else cloudStatus('別タブで更新 · 再読み込み');
  });
  document.getElementById('cloudstatus').onclick=openCloudShelf;
  if(localStorage.getItem(cloudTokenKey)){cloudStatus('同期中');scheduleShelfSync();}
  setInterval(()=>{if(!document.hidden) scheduleShelfSync();},30000);
  window.addEventListener('online',scheduleShelfSync);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden) scheduleShelfSync();});
}
function openCloudShelf(){
  const connected=!!localStorage.getItem(cloudTokenKey);
  const display=v=>v===undefined?'削除':typeof v==='object'?JSON.stringify(v):String(v);
  openWith('<h2>PC・スマホで同じ本棚を使う</h2>'+
    (connected?'<p id="cloudmessage" role="status">'+esc(cloudMessage)+'</p>':
    '<p>初回だけ、各端末を非公開の本棚に接続します。GitHubで対象を <b>bookshelf-private-backup</b> だけにし、Contents を Read and write に設定してください。</p><p><a href="https://github.com/settings/personal-access-tokens/new?name=Bookshelf%20sync&amp;contents=write" target="_blank" rel="noopener noreferrer">GitHubで接続用トークンを作成</a></p><label>この端末の接続用トークン<input id="cloudaccess" type="password" autocomplete="off"></label>')+
    cloudConflicts.map((c,i)=>'<label>'+esc(c.label)+'<select data-conflict="'+i+'"><option value="">選んでください</option><option value="local">この端末：'+esc(display(c.local))+'</option><option value="remote">同期先：'+esc(display(c.remote))+'</option></select></label>').join('')+
    '<div class="menu"><button id="cloudgo">'+(connected?'今すぐ同期':'接続して同期')+'</button>'+(connected?'<button id="cloudstop">この端末の接続を解除</button>':'')+'</div>');
  const shown=cloudConflicts.slice();
  document.getElementById('cloudgo').onclick=async()=>{
    if(!connected){
      const field=document.getElementById('cloudaccess'), token=field.value.trim();
      if(!token.startsWith('github_pat_')) return toast('Fine-grained token を入力してください');
      localStorage.setItem(cloudTokenKey,token);field.value='';
    }
    for(const el of card.querySelectorAll('[data-conflict]')){
      if(!el.value) return toast('違いのある項目を選んでください');
      cloudChoices[shown[Number(el.dataset.conflict)].signature]=el.value;
    }
    await runShelfSync();openCloudShelf();
  };
  if(connected) document.getElementById('cloudstop').onclick=()=>{
    if(cloudBusy)return toast('同期完了後に解除できます');
    localStorage.removeItem(cloudTokenKey);localStorage.removeItem(cloudBaseKey);
    cloudChoices={};cloudConflicts=[];cloudStatus('端末を接続');closeSheet();
  };
}
