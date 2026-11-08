/* Private repository is the canonical shelf. Each device keeps a last-synced
   copy for merging offline edits; GitHub's blob SHA protects concurrent PUTs. */
let loginOrigin='';
async function loadLoginOrigin(){
  if(loginOrigin) return loginOrigin;
  const r=await fetch(new URL('login-service.json',document.baseURI),{cache:'no-store'});
  if(!r.ok) throw Error('ログイン設定を取得できませんでした');
  const url=new URL((await r.json()).origin);
  if(url.protocol!=='https:' || url.username || url.password || url.href!==url.origin+'/') throw Error('ログイン先の設定を確認してください');
  loginOrigin=url.origin;
  return loginOrigin;
}
function authBase64(bytes){
  return btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
}
const proofKey='bookshelf:oauth:proof';
async function startShelfLogin(){
  const origin=await loadLoginOrigin();
  const verifier=authBase64(crypto.getRandomValues(new Uint8Array(32)));
  const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier));
  sessionStorage.setItem(proofKey,verifier);
  location.assign(origin+'/auth/start?challenge='+authBase64(new Uint8Array(hash)));
}
async function finishShelfLogin(){
  if(!location.hash.startsWith('#shelf-login=')) return;
  const ticket=location.hash.slice('#shelf-login='.length);
  history.replaceState(null,'',location.pathname+location.search);
  const verifier=sessionStorage.getItem(proofKey);
  sessionStorage.removeItem(proofKey);
  if(!verifier || ticket==='failed') throw Error('本棚の所有者でログインし、GitHub Appの接続先を確認してください');
  const r=await fetch((await loadLoginOrigin())+'/auth/exchange',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket,verifier})
  });
  if(!r.ok) throw Error('ログインをやり直してください');
  const {session}=await r.json();
  if(!/^[A-Za-z0-9_-]{43}$/.test(session)) throw Error('ログイン応答が無効です');
  localStorage.setItem(cloudTokenKey,session);
  localStorage.removeItem('bookshelf:cloud:access');
}
const cloudTokenKey='bookshelf:cloud:session';
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
  const response=await fetch((await loadLoginOrigin())+'/repo'+path,{...options,cache:'no-store',redirect:'error',
    signal:AbortSignal.timeout(18000),headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'}});
  if(!response.ok){
    if(response.status===401){localStorage.removeItem(cloudTokenKey);throw Error('GitHubで再ログインしてください');}
    const err=new Error(response.status===403||response.status===404
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
async function initCloudShelf(){
  cloudReady=true;
  window.addEventListener('storage',event=>{
    if(event.key!==KEY||!localStorage.getItem(cloudTokenKey)) return;
    shelfChangedInOtherTab=true;
    if(!sheetOpen) location.reload();
    else cloudStatus('別タブで更新 · 再読み込み');
  });
  document.getElementById('cloudstatus').onclick=openCloudShelf;
  try{await finishShelfLogin();}catch(e){toast(e.message,true);}
  if(localStorage.getItem(cloudTokenKey)){cloudStatus('同期中');scheduleShelfSync();}
  else cloudStatus('GitHubでログイン');
  setInterval(()=>{if(!document.hidden) scheduleShelfSync();},30000);
  window.addEventListener('online',scheduleShelfSync);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden) scheduleShelfSync();});
}
function openCloudShelf(){
  const connected=!!localStorage.getItem(cloudTokenKey);
  const display=v=>v===undefined?'削除':typeof v==='object'?JSON.stringify(v):String(v);
  openWith('<h2>PC・スマホで同じ本棚を使う</h2>'+
    (connected?'<p id="cloudmessage" role="status">'+esc(cloudMessage)+'</p>':
    'GitHubの本棚所有者アカウントでログインすると、PCとスマホで同じ蔵書を使えます。')+
    cloudConflicts.map((c,i)=>'<label>'+esc(c.label)+'<select data-conflict="'+i+'"><option value="">選んでください</option><option value="local">この端末：'+esc(display(c.local))+'</option><option value="remote">同期先：'+esc(display(c.remote))+'</option></select></label>').join('')+
    '<div class="menu"><button id="cloudgo">'+(connected?'今すぐ同期':'GitHubでログイン')+'</button>'+(connected?'<button id="cloudstop">この端末の接続を解除</button>':'')+'</div>');
  const shown=cloudConflicts.slice();
  document.getElementById('cloudgo').onclick=async()=>{
    if(!connected){
      try{await startShelfLogin();}catch(e){toast(e.message,true);}
      return;
    }
    for(const el of card.querySelectorAll('[data-conflict]')){
      if(!el.value) return toast('違いのある項目を選んでください');
      cloudChoices[shown[Number(el.dataset.conflict)].signature]=el.value;
    }
    await runShelfSync();openCloudShelf();
  };
  if(connected) document.getElementById('cloudstop').onclick=async()=>{
    if(cloudBusy)return toast('同期完了後に解除できます');
    try{
      const r=await fetch((await loadLoginOrigin())+'/auth/logout',{method:'POST',headers:{Authorization:'Bearer '+localStorage.getItem(cloudTokenKey)}});
      if(!r.ok && r.status!==401) throw Error();
    }catch{return toast('ログアウトできませんでした。接続を確認してください',true);}
    localStorage.removeItem(cloudTokenKey);localStorage.removeItem(cloudBaseKey);
    cloudChoices={};cloudConflicts=[];cloudStatus('GitHubでログイン');closeSheet();
  };
}
