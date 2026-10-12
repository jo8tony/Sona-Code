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
    FakeSocket.instances.push(this);queueMicrotask(()=>{this.handlers.open?.({});this.receive({type:'status',state:'ready',generation:1,capabilities:{implementation:true,references:true}});});}
  addEventListener(name,fn){this.handlers[name]=fn;}
  send(text){const m=JSON.parse(text);this.sent.push(m);if(m.type==='open')this.epochs[m.path]=(this.epochs[m.path]||0)+1;
    if(['definition','implementation','references'].includes(m.type))return;
    queueMicrotask(()=>this.receive({type:m.type==='ping'?'pong':'ack',id:m.id,generation:this.generation,path:m.path,version:m.version}));}
  result(m,extra={}){this.receive({type:m.type+'-result',id:m.id,generation:this.generation,path:m.path,
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
  const implementation=process.argv[2].startsWith('implementation-');
  const references=process.argv[2].startsWith('references-');
  const kind=implementation?'implementation':references?'references':'definition';
  const scenario=implementation?process.argv[2].slice(15):references?process.argv[2].slice(11):process.argv[2];
  if(scenario.startsWith('auto-')) {
    load('workspace-code-navigation');const canvas=new Element('canvas'),opened=[],errors=[],queries=[];
    let current=doc('a.ts',1),position={lineNumber:3,column:5},cursor;
    const client={stamp:()=> '1',subscribe:()=>()=>{}};
    for(const kind of ['definition','implementation','references'])client[kind]=(_source,_point,signal)=>
      new Promise((resolve,reject)=>queries.push({kind,resolve,reject,signal}));
    const editor={getModel:()=>({getWordAtPosition:()=>({word:'value'})}),getPosition:()=>position,
      setPosition:p=>{position=p;cursor?.();},getTargetAtClientPoint:()=>({type:6,position}),
      addAction:()=>({dispose(){}}),onDidChangeCursorPosition:fn=>{cursor=fn;return {dispose(){}};},
      onDidChangeModel:()=>({dispose(){}}),focus(){}};
    const monaco={KeyCode:{F12:59,KeyB:32},KeyMod:{CtrlCmd:2048,Alt:512,Shift:1024},editor:{MouseTargetType:{CONTENT_TEXT:6}},Uri:{from:p=>p},
      languages:Object.fromEntries(['registerDefinitionProvider','registerImplementationProvider','registerReferenceProvider'].map(k=>[k,()=>({dispose(){}})]))};
    const nav=createCodeNavigation({monaco,editor,canvas,client,snapshot:()=>current,openTarget:async(...args)=>{opened.push(args);return true;},onError:m=>errors.push(m)});
    const event=extra=>({ctrlKey:true,button:0,preventDefault(){},stopImmediatePropagation(){},...extra});
    if(scenario==='auto-modifiers') {
      for(const extra of [{shiftKey:true},{altKey:true},{isComposing:true},{button:2},{ctrlKey:false}])canvas.handlers.mousedown(event(extra));
      assert.equal(queries.length,0);
    } else {
      canvas.handlers.mousedown(event());assert.equal(queries[0].kind,'definition');
      if(scenario==='auto-stale'){current=doc('a.ts',2);cursor();assert.ok(queries[0].signal.aborted);}
      queries[0].resolve(scenario==='auto-use'?[{...target('a.ts'),range:{start:{line:0,character:3},end:{line:0,character:8}}}]:[target('a.ts')]);
      await tick();
      if(scenario==='auto-stale')assert.equal(queries.length,1,'No reference query after stale definition result');
      else if(scenario==='auto-use') {
        assert.equal(queries.length,1,'A use in the same file still goes to its definition');
        assert.equal(opened[0][1],'a.ts');assert.equal(opened[0][2].startLineNumber,1);
      } else {
        assert.equal(queries[1].kind,'references');assert.equal(opened.length,0,'Do not navigate back to the declaration');
        if(scenario==='auto-reference-stale'){current={...current,projectId:'other'};cursor();assert.ok(queries[1].signal.aborted);}
        if(scenario==='auto-unsupported')queries[1].reject(Object.assign(new Error('此语言服务暂不支持查找引用'),{code:'unsupported_references'}));
        else queries[1].resolve(scenario==='auto-empty'?[]:[target('use-one.ts'),target('use-two.ts')]);
        await tick();
        if(scenario==='auto-declaration') {
          const menu=canvas.children.find(c=>c.role==='listbox');assert.equal(menu['aria-label'],'选择引用');
          assert.equal(menu.children[0].text,'引用位置（2）');
          menu.handlers.keydown({key:'ArrowDown',preventDefault(){}});menu.handlers.keydown({key:'Enter',preventDefault(){}});await tick();
          assert.equal(opened[0][1],'use-two.ts');assert.equal(opened[0][3].recordHistory,true);
        } else {
          assert.equal(opened.length,0);
          assert.equal(canvas.children.length,0);
          if(scenario==='auto-empty')assert.equal(errors[0],'未找到可打开的引用');
          if(scenario==='auto-unsupported')assert.match(errors[0],/不支持查找引用/);
          if(scenario==='auto-reference-stale')assert.equal(errors.length,0);
        }
      }
    }
    nav.dispose();completed=true;return;
  }
  if(['provider','click','candidates','intent'].includes(scenario)) {
    load('workspace-code-navigation');const canvas=new Element('canvas');const opened=[],errors=[],providers=[],implementations=[],referenceProviders=[];
    let current=doc('a.ts',1),resolve;let calls=0,lastKind;const revision={value:1},subscribers=[];
    const client={stamp:()=>String(revision.value),subscribe:fn=>{subscribers.push(fn);return()=>{};},
      definition:()=>{calls++;lastKind='definition';return new Promise(r=>resolve=r);},implementation:()=>{calls++;lastKind='implementation';return new Promise(r=>resolve=r);},references:()=>{calls++;lastKind='references';return new Promise(r=>resolve=r);}};
    const actions=[];let action,position={lineNumber:1,column:2},cursor,focusCalls=0;
    const model={uri:{toString:()=> 'inmemory://p/a.ts'},getWordAtPosition:()=>({word:'value'}),getLanguageId:()=> 'typescript'};
    const editor={getModel:()=>model,getPosition:()=>position,hasTextFocus:()=>true,
      getTargetAtClientPoint:()=>({type:6,position}),addAction:a=>{actions.push(a);if(a.id==='sona-code.goto-definition')action=a;return {dispose(){}};},
      onDidChangeCursorPosition:fn=>{cursor=fn;return {dispose(){}};},onDidChangeModel:()=>({dispose(){}}),focus(){focusCalls++;}};
    const monaco={KeyCode:{F12:59,KeyB:32},KeyMod:{CtrlCmd:2048,Alt:512,Shift:1024},editor:{MouseTargetType:{CONTENT_TEXT:6}},Uri:{from:parts=>parts},
      languages:{registerImplementationProvider:(language,provider)=>{implementations.push(provider);return {dispose(){}};},registerDefinitionProvider:(language,provider)=>{providers.push(provider);return {dispose(){}};},registerReferenceProvider:(language,provider)=>{referenceProviders.push(provider);return {dispose(){}};}}};
    const nav=createCodeNavigation({monaco,editor,canvas,client,snapshot:()=>current,
      openTarget:async(...args)=>{opened.push(args);return true;},onError:m=>errors.push(m)});
    if(implementation){action=actions.find(a=>a.id==='sona-code.goto-implementation');assert.ok(action,'implementation context action is registered');assert.equal(action.label,'\u8f6c\u5230\u5b9e\u73b0');assert.equal(action.contextMenuGroupId,'navigation');assert.deepEqual(action.keybindings,[2048|512|32]);}
    if(references){action=actions.find(a=>a.id==='sona-code.find-references');assert.equal(action.label,'查找项目引用');assert.deepEqual(action.keybindings,[1024|59]);}
    const provide=()=>references?referenceProviders[0].provideReferences(model,position,{includeDeclaration:false},{isCancellationRequested:false,onCancellationRequested:()=>({dispose(){}})}):
      (implementation?implementations[0].provideImplementation:providers[0].provideDefinition)(model,position,{isCancellationRequested:false,onCancellationRequested:()=>({dispose(){}})});
    if(scenario==='provider') {
      const p=provide();
      resolve([target('unopened.ts')]);const locations=await p;assert.equal(locations[0].uri.path,'/unopened.ts');assert.equal(opened.length,0);assert.equal(errors.length,0);
      const late=provide();
      revision.value++;resolve([target('stale-target.ts')]);assert.deepEqual(await late,[],'provider drops results after a sibling draft changes');
    } else if(scenario==='click') {
      let stopped=0;canvas.handlers.mousedown({ctrlKey:true,button:0,clientX:1,clientY:1,preventDefault(){stopped++;},stopImmediatePropagation(){stopped++;}});
      await tick();resolve([target('unopened.ts')]);await tick();assert.equal(calls,1);assert.equal(stopped,2);assert.equal(opened[0][1],'unopened.ts');
      canvas.handlers.mousedown({ctrlKey:true,button:2});assert.equal(calls,1);
    } else if(scenario==='candidates') {
      const run=action.run();resolve([target('one.ts'),target('two.ts')]);await tick();
      const menu=canvas.children.find(c=>c.role==='listbox');assert.ok(menu);assert.equal(opened.length,0);
      assert.equal(menu['aria-label'],implementation?'选择实现':references?'选择引用':'选择定义');
      menu.handlers.keydown({key:'ArrowDown',preventDefault(){}});menu.handlers.keydown({key:'Enter',preventDefault(){}});await run;
      assert.equal(opened[0][1],'two.ts');assert.ok(menu.removed);
      const again=action.run();resolve([target('one.ts'),target('two.ts')]);await tick();
      const next=canvas.children.filter(c=>c.role==='listbox').at(-1);next.handlers.keydown({key:'Escape',preventDefault(){}});await again;assert.equal(opened.length,1);
      assert.equal(focusCalls,1,'Escape restores text focus');
    } else {
      const run=action.run();current=doc('a.ts',2);cursor({position});resolve([target('late.ts')]);await run;assert.equal(opened.length,0);
      const next=action.run();resolve([target('one.ts'),target('two.ts')]);await tick();revision.value++;subscribers.forEach(fn=>fn());await next;assert.equal(opened.length,0);
    }
    assert.equal(lastKind,kind,'navigation uses the requested semantic query');
    if(opened.length)assert.equal(opened[0][3].recordHistory,true);
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
  const query=async(source=a)=>{const pending=client[kind](source,{line:0,character:1});await tick();return {pending,m:socket.sent.filter(m=>m.type===kind).at(-1)};};
  if(scenario==='unsupported') {
    socket.receive({type:'status',state:'ready',generation:1,capabilities:{implementation:false,references:false}});
    assert.equal((await client[kind](a,{line:0,character:1}).catch(e=>e)).code,'unsupported_'+kind);
    assert.equal(socket.sent.filter(m=>m.type===kind).length,0);
    const q=client.definition(a,{line:0,character:1});await tick();socket.result(socket.sent.filter(m=>m.type==='definition').at(-1));await q;
  } else if(scenario==='mixed-requests') {
    const results=Array.from({length:9},(_,i)=>client[['definition','implementation','references'][i%3]](a,{line:0,character:1}).catch(e=>e));await tick();
    const queries=socket.sent.filter(m=>['definition','implementation','references'].includes(m.type));assert.equal(queries.length,8);
    queries.forEach(m=>socket.result(m));assert.equal((await results.at(-1)).code,'request_limit');await Promise.all(results);
  } else if(scenario==='flush') {
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
    const abort=new AbortController();const pending=client[kind](a,{line:0,character:1},abort.signal).catch(e=>e);await tick();
    const m=socket.sent.filter(m=>m.type===kind).at(-1);abort.abort();assert.equal((await pending).name,'AbortError');
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
    assert.equal((await client[kind](huge,{line:0,character:1}).catch(e=>e)).code,'document_limit');
    const healthy=client[kind](a,{line:0,character:1}).catch(e=>e);await tick();
    const m=socket.sent.filter(m=>m.type===kind).at(-1);
    assert.ok(m,'An oversized sibling must not block a healthy source');
    socket.result(m,{targets:[target('c.ts')]});assert.equal((await healthy)[0].path,'c.ts');
    const mixed=await query();socket.result(mixed.m,{targets:[target('b.ts'),target('c.ts')]});
    assert.deepEqual((await mixed.pending).map(t=>t.path),['c.ts'],'Never use disk positions for paused target drafts');
    const paused=await query();const blocked=paused.pending.catch(e=>e);socket.result(paused.m);
    assert.equal((await blocked).code,'document_limit');
    client.change(doc('b.ts',3,'small again'));await advance(150);
    const restored=await query();socket.result(restored.m);assert.equal((await restored.pending)[0].path,'b.ts');
  } else if(scenario==='unavailable') {
    socket.receive({type:'status',state:'unavailable',generation:1,message:'missing component'});
    const result=client[kind](a,{line:0,character:1}).catch(e=>e);assert.equal((await result).code,'unavailable');
  } else if(scenario==='requests') {
    const results=Array.from({length:9},()=>client[kind](a,{line:0,character:1}).catch(e=>e));await tick();
    const queries=socket.sent.filter(m=>m.type===kind);assert.equal(queries.length,8);
    queries.forEach(m=>socket.result(m));assert.equal((await results.at(-1)).code,'request_limit');await Promise.all(results);
  } else if(scenario==='message-limit') {
    const large=doc('b.ts',2,'\u0001'.repeat(1000000));client.change(large);await advance(150);
    assert.ok(status.some(s=>s.state==='document_limit'));assert.ok(!socket.sent.some(m=>m.text===large.text));
  } else if(scenario==='sync-failure') {
    const original=socket.send.bind(socket);socket.send=text=>{const m=JSON.parse(text);if(m.type!=='change')return original(text);
      socket.sent.push(m);queueMicrotask(()=>socket.receive({type:'error',id:m.id,generation:1,code:'invalid_path',message:'deleted'}));};
    const next=doc('a.ts',2,'new');client.change(next);const result=client[kind](next,{line:0,character:1}).catch(e=>e);
    assert.equal((await result).code,'invalid_path');assert.equal(socket.sent.filter(m=>m.type===kind).length,0);
  } else if(scenario==='timeout') {
    const q=await query();const result=q.pending.catch(e=>e);await advance(15000);assert.equal((await result).code,'timeout');
    assert.ok(socket.sent.some(m=>m.type==='cancel'&&m.request_id===q.m.id));socket.result(q.m);
  } else if(scenario==='cancel-sync'||scenario==='deadline') {
    const original=socket.send.bind(socket);socket.send=text=>{const m=JSON.parse(text);if(m.type!=='change')return original(text);socket.sent.push(m);};
    const aa=doc('a.ts',2,'changed');client.change(aa);const abort=new AbortController();
    const result=client[kind](aa,{line:0,character:1},abort.signal).catch(e=>e);await tick();
    if(scenario==='cancel-sync'){abort.abort();assert.equal((await result).name,'AbortError');}
    else {await advance(15000);assert.equal((await result).code,'timeout');}
    assert.equal(socket.sent.filter(m=>m.type===kind).length,0);
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
