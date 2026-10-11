const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let completed=false;
process.once('beforeExit',()=>{if(!completed){console.error('Scenario did not complete');process.exitCode=1;}});
let now=0, timerId=0;const timers=new Map();
globalThis.setTimeout=(fn,ms=0)=>{timers.set(++timerId,{fn,at:now+ms});return timerId;};
globalThis.clearTimeout=id=>timers.delete(id);
Date.now=()=>now;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function advance(ms){now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);t.fn();}await tick();}
class FakeSocket {
  static OPEN=1;static instances=[];
  constructor(url){this.url=url;this.readyState=1;this.sent=[];this.handlers={};this.generation=1;this.epochs={};
    FakeSocket.instances.push(this);queueMicrotask(()=>{this.handlers.open?.({});this.receive({type:'status',state:'ready',generation:1});});}
  addEventListener(name,fn){this.handlers[name]=fn;}
  send(text){const m=JSON.parse(text);this.sent.push(m);if(m.type==='open')this.epochs[m.path]=(this.epochs[m.path]||0)+1;
    if(m.type==='definition')return;
    queueMicrotask(()=>this.receive({type:m.type==='ping'?'pong':'ack',id:m.id,generation:this.generation,path:m.path,version:m.version}));}
  result(m,extra={}){this.receive({type:'definition-result',id:m.id,generation:this.generation,path:m.path,
    version:m.version,epoch:this.epochs[m.path],targets:[target('b.ts')],...extra});}
  receive(m){this.handlers.message?.({data:JSON.stringify(m)});}
  close(){this.readyState=3;this.handlers.close?.({});}
}
globalThis.WebSocket=FakeSocket;
const doc=(path,version,text='text',epoch=1)=>({projectId:'p',path,language:'typescript',epoch,version,text});
const target=path=>({path,range:{start:{line:2,character:3},end:{line:2,character:8}},origin:null});
const load=name=>vm.runInThisContext(fs.readFileSync('sona_code/web/static/'+name+'.js','utf8'));
class Element {
  constructor(tag,attrs={},children=[]){this.tag=tag;Object.assign(this,attrs);this.children=children;this.handlers={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(name,fn){this.handlers[name]=fn;}
  removeEventListener(name){delete this.handlers[name];}
  setAttribute(name,value){this[name]=value;}
  focus(){this.focused=true;}
  remove(){this.removed=true;}
  showModal(){this.open=true;}close(){this.open=false;this.handlers.close?.({});}
}
globalThis.el=(tag,attrs,...children)=>new Element(tag,attrs,children);
const find=(root,text)=>{if(root.text===text||root.textContent===text)return root;for(const c of root.children||[]){const r=find(c,text);if(r)return r;}};
(async()=>{
  const scenario=process.argv[2];
  if(['provider','click','candidates','intent'].includes(scenario)) {
    load('workspace-code-navigation');const canvas=new Element('canvas');const opened=[],errors=[],providers=[];
    let current=doc('a.ts',1),resolve;let calls=0;const revision={value:1},subscribers=[];
    const client={stamp:()=>String(revision.value),subscribe:fn=>{subscribers.push(fn);return()=>{};},
      definition:()=>{calls++;return new Promise(r=>resolve=r);}};
    let action,position={lineNumber:1,column:2},cursor,focusCalls=0;
    const model={uri:{toString:()=> 'inmemory://p/a.ts'},getWordAtPosition:()=>({word:'value'}),getLanguageId:()=> 'typescript'};
    const editor={getModel:()=>model,getPosition:()=>position,hasTextFocus:()=>true,
      getTargetAtClientPoint:()=>({type:6,position}),addAction:a=>{action=a;return {dispose(){}};},
      onDidChangeCursorPosition:fn=>{cursor=fn;return {dispose(){}};},onDidChangeModel:()=>({dispose(){}}),focus(){focusCalls++;}};
    const monaco={KeyCode:{F12:59},editor:{MouseTargetType:{CONTENT_TEXT:6}},Uri:{from:parts=>parts},
      languages:{registerDefinitionProvider:(language,provider)=>{providers.push(provider);return {dispose(){}};}}};
    const nav=createCodeNavigation({monaco,editor,canvas,client,snapshot:()=>current,
      openTarget:async(...args)=>{opened.push(args);return true;},onError:m=>errors.push(m)});
    if(scenario==='provider') {
      const p=providers[0].provideDefinition(model,position,{isCancellationRequested:false,onCancellationRequested:()=>({dispose(){}})});
      resolve([target('unopened.ts')]);const locations=await p;assert.equal(locations[0].uri.path,'/unopened.ts');assert.equal(opened.length,0);assert.equal(errors.length,0);
    } else if(scenario==='click') {
      let stopped=0;canvas.handlers.mousedown({ctrlKey:true,button:0,clientX:1,clientY:1,preventDefault(){stopped++;},stopImmediatePropagation(){stopped++;}});
      await tick();resolve([target('unopened.ts')]);await tick();assert.equal(calls,1);assert.equal(stopped,2);assert.equal(opened[0][1],'unopened.ts');
      canvas.handlers.mousedown({ctrlKey:true,button:2});assert.equal(calls,1);
    } else if(scenario==='candidates') {
      const run=action.run();resolve([target('one.ts'),target('two.ts')]);await tick();
      const menu=canvas.children.find(c=>c.role==='listbox');assert.ok(menu);assert.equal(opened.length,0);
      menu.handlers.keydown({key:'ArrowDown',preventDefault(){}});menu.handlers.keydown({key:'Enter',preventDefault(){}});await run;
      assert.equal(opened[0][1],'two.ts');assert.ok(menu.removed);
      const again=action.run();resolve([target('one.ts'),target('two.ts')]);await tick();
      const next=canvas.children.filter(c=>c.role==='listbox').at(-1);next.handlers.keydown({key:'Escape',preventDefault(){}});await again;assert.equal(opened.length,1);
      assert.equal(focusCalls,1,'Escape restores text focus');
    } else {
      const run=action.run();current=doc('a.ts',2);cursor({position});resolve([target('late.ts')]);await run;assert.equal(opened.length,0);
      const next=action.run();resolve([target('one.ts'),target('two.ts')]);await tick();revision.value++;subscribers.forEach(fn=>fn());await next;assert.equal(opened.length,0);
    }
    nav.dispose();assert.equal(canvas.handlers.mousedown,undefined);completed=true;return;
  }
  if(scenario==='settings') {
    load('workspace-analysis-settings');globalThis.document={body:new Element('body')};const requests=[],saved=[],errors=[];
    const settings=createAnalysisSettings({api:(...args)=>new Promise((resolve,reject)=>requests.push({args,resolve,reject})),
      projectId:()=> 'p',onSaved:id=>saved.push(id),onError:m=>errors.push(m)});
    const opening=settings.open();requests.shift().resolve({java:{mode:'source',source_roots:[],libraries:[],project_jdk:null,gradle_home:null},python:{interpreter:null}});await opening;
    const dialog=document.body.children[0],form=dialog.children.find(c=>c.tag==='form');
    find(dialog,'保存设置').onclick({preventDefault(){}});requests.shift().reject({detail:'bad path'});await tick();assert.equal(dialog.open,true);assert.equal(saved.length,0);
    find(dialog,'保存设置').onclick({preventDefault(){}});const req=requests.shift();assert.equal(req.args[1].body.java.mode,'source');req.resolve({});await tick();assert.deepEqual(saved,['p']);
    settings.dispose();assert.ok(dialog.removed);completed=true;return;
  }
  load('workspace-language-client');const errors=[],status=[];
  const client=createLanguageClient({urlFor:(p,f,id)=>`ws://local/custom/api/${p}/${f}?editor_id=${id}`,
    onStatus:s=>status.push(s),onError:m=>errors.push(m)});
  const a=doc('a.ts',1),b=doc('b.ts',1);client.open(a);client.open(b);client.setActive('p','typescript');await tick();
  let socket=FakeSocket.instances[0];assert.match(socket.url,/custom\/api\/p\/typescript/);
  const query=async(source=a)=>{const pending=client.definition(source,{line:0,character:1});await tick();return {pending,m:socket.sent.filter(m=>m.type==='definition').at(-1)};};
  if(scenario==='flush') {
    const aa=doc('a.ts',2,'changed a'),bb=doc('b.ts',2,'changed b');client.change(aa);client.change(bb);
    const {pending,m}=await query(aa);const before=socket.sent.slice(0,socket.sent.indexOf(m));assert.equal(before.filter(m=>m.type==='change').length,2);
    socket.result(m);await pending;client.change(doc('b.ts',3,'target moved'));const q=await query(aa);socket.result(q.m);await q.pending;assert.notEqual(q.m.id,m.id);
  } else if(scenario==='debounce') {
    client.change(doc('a.ts',2,'first'));client.change(doc('a.ts',3,'last'));await advance(149);assert.equal(socket.sent.filter(m=>m.type==='change').length,0);
    await advance(1);const changes=socket.sent.filter(m=>m.type==='change');assert.equal(changes.length,1);assert.equal(changes[0].text,'last');
  } else if(scenario==='stale') {
    const q=await query();const result=q.pending.catch(e=>e);client.change(doc('b.ts',2,'new target'));socket.result(q.m);assert.equal((await result).code,'stale');
  } else if(scenario==='epoch') {
    const q=await query();const result=q.pending.catch(e=>e);client.close('p','a.ts');client.open(doc('a.ts',2,'new model',2));socket.result(q.m);assert.equal((await result).code,'stale');
    await tick();const next=await query(doc('a.ts',2,'new model',2));socket.result(next.m);assert.equal((await next.pending)[0].path,'b.ts');
  } else if(scenario==='generation') {
    const q=await query();const result=q.pending.catch(e=>e);socket.generation=2;socket.receive({type:'status',state:'initializing',generation:2});
    socket.result(q.m,{generation:1});assert.equal((await result).code,'stale');
  } else if(scenario==='cancel') {
    const abort=new AbortController();const pending=client.definition(a,{line:0,character:1},abort.signal).catch(e=>e);await tick();
    const m=socket.sent.filter(m=>m.type==='definition').at(-1);abort.abort();assert.equal((await pending).name,'AbortError');
    const cancel=socket.sent.find(m=>m.type==='cancel');assert.equal(cancel.request_id,m.id);assert.ok(cancel.id>m.id);socket.result(m);
  } else if(scenario==='reconnect') {
    client.change(doc('a.ts',2,'latest'));socket.close();await advance(1000);socket=FakeSocket.instances.at(-1);await tick();
    assert.equal(socket.sent.find(m=>m.type==='open'&&m.path==='a.ts').text,'latest');
    const q=await query(doc('a.ts',2,'latest'));socket.result(q.m);await q.pending;
  } else if(scenario==='idle') {
    await advance(20000);assert.ok(socket.sent.some(m=>m.type==='ping'));client.setActive('p',null);
    await advance(180000);assert.equal(socket.readyState,3);assert.equal(FakeSocket.instances.length,1);
    client.setActive('p','typescript');await tick();assert.equal(FakeSocket.instances.length,2);
  } else if(scenario==='limit') {
    const huge=doc('b.ts',2,'中'.repeat(800000));client.change(huge);
    assert.ok(status.some(s=>s.state==='document_limit'));assert.ok(!socket.sent.some(m=>m.text===huge.text));
    const result=client.definition(a,{line:0,character:1}).catch(e=>e);assert.equal((await result).code,'document_limit');
  } else if(scenario==='unavailable') {
    socket.receive({type:'status',state:'unavailable',generation:1,message:'missing component'});
    const result=client.definition(a,{line:0,character:1}).catch(e=>e);assert.equal((await result).code,'unavailable');
  } else if(scenario==='requests') {
    const results=Array.from({length:9},()=>client.definition(a,{line:0,character:1}).catch(e=>e));await tick();
    const queries=socket.sent.filter(m=>m.type==='definition');assert.equal(queries.length,8);
    queries.forEach(m=>socket.result(m));assert.equal((await results.at(-1)).code,'request_limit');await Promise.all(results);
  } else if(scenario==='message-limit') {
    const large=doc('b.ts',2,'\u0001'.repeat(1000000));client.change(large);await advance(150);
    assert.ok(status.some(s=>s.state==='document_limit'));assert.ok(!socket.sent.some(m=>m.text===large.text));
  } else if(scenario==='sync-failure') {
    const original=socket.send.bind(socket);socket.send=text=>{const m=JSON.parse(text);if(m.type!=='change')return original(text);
      socket.sent.push(m);queueMicrotask(()=>socket.receive({type:'error',id:m.id,generation:1,code:'invalid_path',message:'deleted'}));};
    const next=doc('a.ts',2,'new');client.change(next);const result=client.definition(next,{line:0,character:1}).catch(e=>e);
    assert.equal((await result).code,'invalid_path');assert.equal(socket.sent.filter(m=>m.type==='definition').length,0);
  } else if(scenario==='timeout') {
    const q=await query();const result=q.pending.catch(e=>e);await advance(15000);assert.equal((await result).code,'timeout');
    assert.ok(socket.sent.some(m=>m.type==='cancel'&&m.request_id===q.m.id));socket.result(q.m);
  } else if(scenario==='cancel-sync'||scenario==='deadline') {
    const original=socket.send.bind(socket);socket.send=text=>{const m=JSON.parse(text);if(m.type!=='change')return original(text);socket.sent.push(m);};
    const aa=doc('a.ts',2,'changed');client.change(aa);const abort=new AbortController();
    const result=client.definition(aa,{line:0,character:1},abort.signal).catch(e=>e);await tick();
    if(scenario==='cancel-sync'){abort.abort();assert.equal((await result).name,'AbortError');}
    else {await advance(15000);assert.equal((await result).code,'timeout');}
    assert.equal(socket.sent.filter(m=>m.type==='definition').length,0);
  } else if(scenario==='empty-reanalyze') {
    client.close('p','a.ts');client.close('p','b.ts');client.reanalyze('p','typescript');await tick();assert.equal(FakeSocket.instances.length,1);
  } else if(scenario==='boundary-error') {
    const q=await query();const result=q.pending.catch(e=>e);
    socket.receive({type:'error',id:q.m.id,code:'invalid_path',message:'file missing'});assert.equal((await result).code,'invalid_path');
  } else if(scenario==='capacity-status') {
    socket.receive({type:'error',id:null,code:'service_limit',message:'已有 6 个语言服务在运行'});
    assert.ok(status.some(s=>s.state==='service_limit'&&s.message.includes('6')));
  } else throw Error('Unknown client scenario');
  client.dispose();assert.equal(timers.size,0);socket.receive({type:'status',state:'ready',generation:99});assert.equal(FakeSocket.instances.length>=1,true);
  console.log('passed',scenario);
  completed=true;
})().catch(error=>{console.error(error);process.exitCode=1;});
