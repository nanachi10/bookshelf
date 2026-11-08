import test from 'node:test';
import assert from 'node:assert/strict';
import {ShelfSession} from './worker.mjs';

test('owner login, proof-bound single use ticket, private file access and logout', async()=>{
  const rows=new Map();
  const storage={get:k=>rows.get(k),put:(k,v)=>rows.set(k,v),delete:k=>rows.delete(k),getAlarm:()=>1,transaction:fn=>fn(storage)};
  const service=new ShelfSession({storage,blockConcurrencyWhile:fn=>fn()},{CLIENT_ID:'test',CLIENT_SECRET:'test'});
  const fetchReal=globalThis.fetch;
  let who=128808398, privateRepo=true;
  globalThis.fetch=async url=>{
    if(url.includes('access_token')) return Response.json({access_token:'server-only'});
    if(url.endsWith('/user'))return Response.json({id:who});
    if(url.endsWith('private-backup'))return Response.json({private:privateRepo});
    return Response.json({sha:'snapshot'});
  };
  const origin='https://nanachi10.github.io', host='https://test.workers.dev';
  const call=(path,init={})=>service.fetch(new Request(host+path,init));
  const verifier='v'.repeat(43);
  const challenge=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))).toString('base64url');
  async function callback(){
    const start=await call('/auth/start?challenge='+challenge);
    const state=new URL(start.headers.get('location')).searchParams.get('state');
    const path='/auth/callback?code=test&state='+state;
    assert.equal((await call(path)).status,401);
    return call(path,{headers:{Cookie:'__Host-shelf-state='+state}});
  }
  try{
    who=1;
    assert.match((await callback()).headers.get('location'),/login=failed$/);
    who=128808398;
    const ticket=new URL((await callback()).headers.get('location')).hash.split('=')[1];
    const exchange=(proof)=>call('/auth/exchange',{method:'POST',headers:{Origin:origin},body:JSON.stringify({ticket,verifier:proof})});
    assert.equal((await exchange('x'.repeat(43))).status,401);
    const {session}=await (await exchange(verifier)).json();
    assert.equal(session.length,43);
    assert.equal((await exchange(verifier)).status,401);
    const headers={Origin:origin,Authorization:'Bearer '+session};
    assert.equal((await call('/repo/contents/live-shelf.json',{headers})).status,200);
    assert.equal((await call('/repo/contents/other-file',{headers})).status,404);
    assert.equal((await call('/repo',{headers:{...headers,Origin:'https://example.org'}})).status,403);
    privateRepo=false;assert.equal((await call('/repo',{headers})).status,403);
    await call('/auth/logout',{method:'POST',headers});
    assert.equal((await call('/repo',{headers})).status,401);
  }finally{globalThis.fetch=fetchReal;}
});
