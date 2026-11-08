const APP='https://nanachi10.github.io/bookshelf/';
const ORIGIN=new URL(APP).origin;
const OWNER=128808398;
const REPO='https://api.github.com/repos/nanachi10/bookshelf-private-backup';
const encoder=new TextEncoder();
const random=()=>base64(crypto.getRandomValues(new Uint8Array(32)));
const base64=bytes=>btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
const digest=async value=>base64(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value))));
const valid=value=>typeof value==='string' && /^[A-Za-z0-9_-]{43}$/.test(value);
const json=(value,status=200)=>Response.json(value,{status,headers:{'Cache-Control':'no-store'}});
class HttpError extends Error{constructor(status){super();this.status=status;}}
const requireValue=(condition,status=400)=>{if(!condition) throw new HttpError(status);};
function redirect(url,cookie){
  const headers={'Location':url,'Cache-Control':'no-store','Referrer-Policy':'no-referrer'};
  if(cookie) headers['Set-Cookie']=cookie;
  return new Response(null,{status:302,headers});
}
const cookie=value=>'__Host-shelf-state='+value+'; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age='+(value?600:0);
async function oauth(env,params){
  const r=await fetch('https://github.com/login/oauth/access_token',{method:'POST',
    headers:{Accept:'application/json','Content-Type':'application/json'},
    body:JSON.stringify({client_id:env.CLIENT_ID,client_secret:env.CLIENT_SECRET,...params})});
  const v=await r.json();
  requireValue(r.ok && v.access_token,401);
  return {access:v.access_token,refresh:v.refresh_token,until:v.expires_in?Date.now()+v.expires_in*1000:Infinity};
}
function github(url,access,options={}){
  return fetch(url,{...options,redirect:'error',headers:{'User-Agent':'nanachi-bookshelf','Accept':'application/vnd.github+json',
    'Authorization':'Bearer '+access,'Content-Type':'application/json'}});
}
export class ShelfSession{
  constructor(ctx,env){this.ctx=ctx;this.env=env;this.db=ctx.storage;}
  async put(id,value,ttl){
    await this.db.put(id,{...value,expires:Date.now()+ttl});
    if(await this.db.getAlarm()===null) await this.db.setAlarm(Date.now()+600000);
  }
  async alarm(){
    const entries=await this.db.list();
    const expired=[...entries].filter(([,v])=>v.expires<=Date.now()).map(([k])=>k);
    if(expired.length) await this.db.delete(expired);
    if(entries.size>expired.length) await this.db.setAlarm(Date.now()+600000);
  }
  async take(id,check){
    return this.db.transaction(async tx=>{
      const v=await tx.get(id);
      requireValue(v && v.expires>Date.now() && check(v),401);
      await tx.delete(id);return v;
    });
  }
  async fetch(req){
    try{return await this.route(req);}catch(e){return json({error:'request failed'},e.status||502);}
  }
  async route(req){
    const url=new URL(req.url), path=url.pathname;
    const callback=url.origin+'/auth/callback', q=url.searchParams;
    if(path==='/auth/start' && req.method==='GET'){
      const client=q.get('challenge');requireValue(valid(client));
      const state=random(),verifier=random();
      await this.put('flow:'+state,{client,verifier},600000);
      const query=new URLSearchParams({client_id:this.env.CLIENT_ID,redirect_uri:callback,state,
        code_challenge_method:'S256',code_challenge:await digest(verifier)});
      return redirect('https://github.com/login/oauth/authorize?'+query,cookie(state));
    }
    if(path==='/auth/callback' && req.method==='GET'){
      const state=q.get('state');
      requireValue(valid(state) && (req.headers.get('Cookie')||'').split(/;\s*/).includes('__Host-shelf-state='+state),401);
      const flow=await this.take('flow:'+state,()=>true);
      try{
        const tokens=await oauth(this.env,{code:q.get('code'),code_verifier:flow.verifier,redirect_uri:callback});
        const user=await github('https://api.github.com/user',tokens.access);
        requireValue(user.ok && (await user.json()).id===OWNER,403);
        const repo=await github(REPO,tokens.access);
        requireValue(repo.ok && (await repo.json()).private===true,403);
        const ticket=random();
        await this.put('ticket:'+ticket,{client:flow.client,tokens},120000);
        return redirect(APP+'#shelf-login='+ticket,cookie(''));
      }catch{return redirect(APP+'#shelf-login=failed',cookie(''));}
    }
    requireValue(req.headers.get('Origin')===ORIGIN,403);
    if(path==='/auth/exchange' && req.method==='POST'){
      const {ticket,verifier}=await req.json();requireValue(valid(ticket)&&valid(verifier));
      const proof=await digest(verifier);
      const record=await this.take('ticket:'+ticket,v=>v.client===proof);
      const session=random();
      await this.put('session:'+await digest(session),{tokens:record.tokens},30*86400000);
      return json({session});
    }
    const sid=(req.headers.get('Authorization')||'').replace(/^Bearer /,'');
    requireValue(valid(sid),401);
    const id='session:'+await digest(sid);
    if(path==='/auth/logout' && req.method==='POST'){await this.db.delete(id);return json({ok:true});}
    const access=await this.ctx.blockConcurrencyWhile(async()=>{
      const v=await this.db.get(id);
      requireValue(v && v.expires>Date.now(),401);
      if(v.tokens.until<Date.now()+60000){
        requireValue(v.tokens.refresh,401);
        v.tokens=await oauth(this.env,{grant_type:'refresh_token',refresh_token:v.tokens.refresh});
        await this.db.put(id,v);
      }
      return v.tokens.access;
    });
    const file='/contents/live-shelf.json';
    const read=req.method==='GET' && (path==='/repo' || path==='/repo'+file || /^\/repo\/git\/blobs\/[0-9a-f]{40}$/.test(path));
    const write=req.method==='PUT' && path==='/repo'+file;
    requireValue(read||write,404);
    const repo=await github(REPO,access);
    requireValue(repo.ok && (await repo.json()).private===true,403);
    if(path==='/repo') return json({private:true});
    let options={};
    if(write){
      const {sha,content}=await req.json();
      requireValue(/^[a-f0-9]{40}$/.test(sha)&&typeof content==='string'&&content.length<12000000);
      const raw=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(content),c=>c.charCodeAt(0))));
      requireValue(raw.app==='bookshelf'&&Array.isArray(raw.books));
      options={method:'PUT',body:JSON.stringify({message:'本棚を同期',branch:'main',content,sha})};
    }
    const resource=path.slice(5)+(read&&path.endsWith(file)?'?ref=main':'');
    const upstream=await github(REPO+resource,access,options);
    if(!upstream.ok)return json({error:'GitHub request failed'},upstream.status);
    return new Response(upstream.body,{headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
  }
}
export default {async fetch(req,env){
  const origin=req.headers.get('Origin');
  if(origin && origin!==ORIGIN) return json({},403);
  const res=req.method==='OPTIONS'?new Response(null,{status:204}):
    await env.SHELF.get(env.SHELF.idFromName('owner')).fetch(req);
  if(origin!==ORIGIN)return res;
  const out=new Response(res.body,res);
  out.headers.set('Access-Control-Allow-Origin',ORIGIN);
  out.headers.set('Access-Control-Allow-Methods','GET, POST, PUT, OPTIONS');
  out.headers.set('Access-Control-Allow-Headers','Authorization, Content-Type');
  out.headers.set('Vary','Origin');
  return out;
}};
