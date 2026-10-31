import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ASSURANCE_VERSION,validateAssuranceManifest,validateAssuranceReport,mergeAssuranceReports,assessAssuranceReport,assessMergedAssurance,assuranceManifestJSON,assuranceReportJSON,mergedAssuranceJSON,parseAssuranceManifestJSON,parseAssuranceReportJSON,parseMergedAssuranceJSON,AssuranceObserver,assuredFuzz,assuredEnumerate,assuredGuidedFuzz,assuredCampaign,assuredGuidedCampaign,DEFAULT_GUIDANCE,accepted,check,fuzz,ModelError} from '../dist/index.js';
import {runNodeAssuredCampaign,runNodeAssuredGuidedCampaign} from '../dist/node_campaign.js';
import {AssuranceReportStore} from '../dist/assurance.js';

const manifest=()=>({assuranceVersion:ASSURANCE_VERSION,model:'m',requiredProperties:[{id:'safe',phase:'State'},{id:'safe',phase:'Transition'}],inputFamilies:[{name:'input',coverage:{kind:'Required'}},{name:'external',coverage:{kind:'Excluded',reason:'outside model'}}],bounds:{queue:'2'},coverGoals:[{id:'reached',scope:'History'}],requiredSearchOutcome:'GraphExhausted',provenance:{manifest:'v1',model:'m1',properties:'p1'}});
test('assurance manifest validates independent phase identities and owns declarations',()=>{
 const input=manifest(),owned=validateAssuranceManifest(input);input.requiredProperties[0].id='changed';input.inputFamilies[1].coverage.reason='changed';input.bounds.queue='8';input.coverGoals[0].id='changed';input.provenance.model='changed';
 assert.equal(owned.requiredProperties[0].id,'safe');assert.equal(owned.inputFamilies[1].coverage.reason,'outside model');assert.equal(owned.bounds.queue,'2');assert.equal(owned.coverGoals[0].id,'reached');assert.equal(owned.provenance.model,'m1');
});
test('assured campaign retains observations across delivery error and cancellation',async()=>{
 const m=manifest();m.requiredSearchOutcome='Completed';m.coverGoals[0].scope='Transition';const metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'};
 const model=()=>({metadata:()=>metadata,initialState:()=>0,step:()=>accepted(0),checkState:()=>[check('safe','passed')],checkTransition:()=>[check('safe','passed')],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,generate:()=>1,isEnabled:()=>true});
 const config={masterSeed:1n,firstJob:5n,jobs:2,workers:1,resultBuffer:1,stopOnFailure:false,fuzz:{seed:0n,cases:1,maxSteps:1,maxTransitions:2n,mutationPercent:0}},coverage=()=>((_s,_i,_t,_h,hits)=>{hits.family('input');hits.goal('reached');});
 const delivered=[];const complete=await assuredCampaign(config,{},m,metadata,model,coverage,j=>{delivered.push(j.jobId);});assert.deepEqual(delivered,[5n,6n]);assert.equal(complete.assurance.report.outcome,'CasesCompleted');assert.equal(complete.assurance.jobs.size,2);validateAssuranceReport(complete.assurance.report);
 const capped=await assuredCampaign(config,{},m,metadata,model,coverage,()=>{},{observationLimits:{maxHitsPerTransition:1}});assert.equal(capped.assurance.report.budgets['assurance.max_hits_per_transition'],undefined);assert.match(capped.assurance.jobs.get(5n).callbackErrors.join(' '),/incomplete/);let started=0;await assert.rejects(()=>assuredCampaign(config,{},m,metadata,()=>{started++;return model();},coverage,()=>{},{observationLimits:{maxManifestEntries:1}}),/manifest entry limit/);assert.equal(started,0);
 let received=0;const retained=await assuredCampaign(config,{},m,metadata,model,coverage,()=>{received++;},{maxRetainedReports:1});assert.equal(received,2);assert.equal(retained.assurance.jobs.size,1);assert.match(retained.assurance.report.callbackErrors.join(' '),/retained evidence limit/);assert.equal(retained.assurance.retention.droppedReports,1);assert.equal(retained.assurance.report.budgets['assurance.dropped_reports'],undefined);assert.equal(parseMergedAssuranceJSON(mergedAssuranceJSON(retained.assurance)).missingJobs.length,1);
 const noBytes=await assuredCampaign(config,{},m,metadata,model,coverage,()=>{},{maxRetainedBytes:0});assert.equal(noBytes.assurance.jobs.size,0);assert.equal(noBytes.assurance.retention.droppedReports,2);assert.ok(Object.keys(noBytes.assurance.report.budgets).every(k=>!k.startsWith('assurance.')));
 assert.equal(parseMergedAssuranceJSON(mergedAssuranceJSON(complete.assurance)).report.outcome,'CasesCompleted');
 assert.throws(()=>parseAssuranceReportJSON(assuranceReportJSON(complete.assurance.report)),/constituent job proof/);
 const forged=JSON.parse(mergedAssuranceJSON(complete.assurance));delete forged.jobs['6'];forged.missing_jobs=[6];forged.report.search.budgets.reported_jobs='1';assert.throws(()=>parseMergedAssuranceJSON(JSON.stringify(forged)),/job evidence|complete job/);
 const incomplete=JSON.parse(mergedAssuranceJSON(complete.assurance));incomplete.report.search.budgets['campaign.delivered']='0';assert.throws(()=>parseMergedAssuranceJSON(JSON.stringify(incomplete)),/delivery proof/);
 const wrongCount=JSON.parse(mergedAssuranceJSON(complete.assurance));wrongCount.report.search.budgets.requested_jobs='0';assert.throws(()=>parseMergedAssuranceJSON(JSON.stringify(wrongCount)),/job accounting/);
 const nested=JSON.parse(mergedAssuranceJSON(complete.assurance));nested.jobs['5'].search.outcome='CasesCompleted';nested.jobs['5'].search.termination='MergedJobs';assert.throws(()=>parseMergedAssuranceJSON(JSON.stringify(nested)),/constituent job proof/);
 const failed=await assuredCampaign(config,{},m,metadata,model,coverage,()=>{throw Error('sink broke');});assert.equal(failed.campaign.termination.kind,'CallbackError');assert.equal(failed.assurance.report.outcome,'Failed');assert.equal(failed.assurance.jobs.size,1);assert.deepEqual(failed.assurance.missingJobs,[6n]);assert.match(failed.assurance.report.callbackErrors.join(' '),/sink broke/);
 assert.match(failed.assurance.report.termination,/^Campaign::CallbackError\(ModelError\("/);assert.equal(parseMergedAssuranceJSON(mergedAssuranceJSON(failed.assurance)).report.outcome,'Failed');
 const unusual=await assuredCampaign(config,{},m,metadata,model,coverage,()=>{throw Error('quote " slash \\ newline\n');});const unusualWire=mergedAssuranceJSON(unusual.assurance),imported=parseMergedAssuranceJSON(unusualWire);assert.equal(imported.report.termination,unusual.assurance.report.termination);assert.match(imported.report.termination,/\\n/);
 const invalidTermination=JSON.parse(unusualWire);invalidTermination.report.search.termination='Campaign::CallbackError(ModelError(unquoted))';assert.throws(()=>parseMergedAssuranceJSON(JSON.stringify(invalidTermination)),/termination/);
 const cancelled=await assuredCampaign(config,{cancellation:()=>true},m,metadata,model,coverage,()=>{});assert.equal(cancelled.campaign.termination.kind,'Cancelled');assert.equal(cancelled.assurance.report.outcome,'Interrupted');assert.equal(cancelled.assurance.jobs.size,0);
 const controls={stop:true,cancellation(){assert.equal(this,controls);return this.stop;}};const bound=await assuredCampaign(config,controls,m,metadata,model,coverage,()=>{});assert.equal(bound.campaign.termination.kind,'Cancelled');assert.equal(bound.assurance.report.budgets.cancellation_enabled,'true');
 const identity=await assuredCampaign(config,{},m,metadata,()=>({...model(),metadata:()=>({...metadata,build:'different'})}),coverage,()=>{});assert.equal(identity.assurance.jobs.size,0);assert.deepEqual(identity.assurance.missingJobs,[5n,6n]);
 assert.equal(parseMergedAssuranceJSON(mergedAssuranceJSON(identity.assurance)).missingJobs.length,2);
 let disposed=0;const feedback=()=>({metadata:()=>({name:'guided',version:1,build:'test'}),cloneFeature:x=>x,equalFeatures:(a,b)=>a===b,transitionFeatures:(_b,_i,_t,out)=>out.push('seen'),dispose:()=>{disposed++;}});
 const guided=await assuredGuidedCampaign(config,{},m,metadata,model,coverage,DEFAULT_GUIDANCE,feedback,()=>{});assert.equal(guided.assurance.report.outcome,'CasesCompleted');assert.equal(guided.assurance.jobs.get(5n).budgets['guidance.feedback.name'],'guided');assert.equal(disposed,2);
 const guidedCapped=await assuredGuidedCampaign(config,{},m,metadata,model,coverage,DEFAULT_GUIDANCE,feedback,()=>{},{observationLimits:{maxHitsPerTransition:1}});assert.match(guidedCapped.assurance.jobs.get(5n).callbackErrors.join(' '),/incomplete/);assert.equal(guidedCapped.assurance.report.budgets['assurance.max_hits_per_transition'],undefined);disposed-=2;
 const guidedFail=await assuredGuidedCampaign(config,{},m,metadata,model,coverage,DEFAULT_GUIDANCE,feedback,()=>{throw Error('guided sink');});assert.equal(guidedFail.assurance.report.outcome,'Failed');assert.equal(guidedFail.assurance.jobs.size,1);assert.equal(disposed,3);
 const guidedCancelled=await assuredGuidedCampaign(config,{cancellation:()=>true},m,metadata,model,coverage,DEFAULT_GUIDANCE,feedback,()=>{});assert.equal(guidedCancelled.assurance.report.outcome,'Interrupted');assert.equal(disposed,3);
 const supplied={...metadata};const ownedModel=()=>({...model(),metadata:()=>({...metadata})});const mutation=await assuredCampaign(config,{},m,supplied,ownedModel,coverage,()=>{supplied.build='changed';});assert.equal(mutation.assurance.jobs.size,2);assert.equal(mutation.assurance.report.metadata.build,'test');
});
test('Node workers retain assurance independently of delivery',async()=>{
 const m=manifest();m.requiredSearchOutcome='Completed';m.coverGoals[0].scope='Transition';const metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'};
 const config={masterSeed:1n,firstJob:5n,jobs:4,workers:2,resultBuffer:1,stopOnFailure:false,fuzz:{seed:0n,cases:1,maxSteps:1,maxTransitions:2n,mutationPercent:0}},worker={module:new URL('./fixtures/assurance-worker.mjs',import.meta.url),decodeInput:x=>x};
 const complete=await runNodeAssuredCampaign(config,{},m,metadata,worker,()=>{});assert.equal(complete.assurance.jobs.size,4);assert.equal(complete.assurance.report.outcome,'CasesCompleted');validateAssuranceReport(complete.assurance.report);
 const blocked={...worker,data:{barrier:new SharedArrayBuffer(4)}};
 const failed=await runNodeAssuredCampaign(config,{},m,metadata,blocked,async()=>{await new Promise(resolve=>setTimeout(resolve,100));throw Error('delivery broke');});assert.equal(failed.campaign.termination.kind,'CallbackError');assert.equal(failed.assurance.report.outcome,'Failed');assert.ok(failed.assurance.jobs.size>=2);assert.match(failed.assurance.report.callbackErrors.join(' '),/delivery broke/);
 const guided=await runNodeAssuredGuidedCampaign(config,{},m,metadata,DEFAULT_GUIDANCE,worker,()=>{});assert.equal(guided.assurance.jobs.size,4);assert.equal(guided.assurance.report.outcome,'CasesCompleted');assert.equal(guided.assurance.jobs.get(5n).budgets['guidance.feedback.name'],'guided');
 const nodeCapped=await runNodeAssuredCampaign(config,{},m,metadata,worker,()=>{},{observationLimits:{maxHitsPerTransition:1}});assert.match(nodeCapped.assurance.jobs.get(5n).callbackErrors.join(' '),/incomplete/);assert.equal(nodeCapped.assurance.report.budgets['assurance.max_hits_per_transition'],undefined);
 let delivered=0;const nodeRetained=await runNodeAssuredCampaign(config,{},m,metadata,worker,()=>{delivered++;},{maxRetainedReports:1});assert.equal(delivered,4);assert.equal(nodeRetained.assurance.jobs.size,1);assert.equal(nodeRetained.assurance.retention.droppedReports,3);assert.match(nodeRetained.assurance.report.callbackErrors.join(' '),/retained evidence limit/);
 const nodeGuidedCapped=await runNodeAssuredGuidedCampaign(config,{},m,metadata,DEFAULT_GUIDANCE,worker,()=>{},{observationLimits:{maxHitsPerTransition:1}});assert.match(nodeGuidedCapped.assurance.jobs.get(5n).callbackErrors.join(' '),/incomplete/);await assert.rejects(()=>runNodeAssuredCampaign(config,{},m,metadata,worker,()=>{},{observationLimits:{maxManifestBytes:1}}),/manifest byte limit/);
 const localCapped=await runNodeAssuredGuidedCampaign({...config,workers:1},{},m,metadata,DEFAULT_GUIDANCE,worker,()=>{},{observationLimits:{maxHitsPerTransition:1}});assert.match(localCapped.assurance.jobs.get(5n).callbackErrors.join(' '),/incomplete/);
 const guidedLocal=await runNodeAssuredGuidedCampaign({...config,workers:1},{},m,metadata,DEFAULT_GUIDANCE,worker,()=>{});assert.equal(guidedLocal.assurance.jobs.size,4);
 const controls={stop:true,cancellation(){assert.equal(this,controls);return this.stop;}};const receiver=await runNodeAssuredCampaign({...config,workers:1},controls,m,metadata,worker,()=>{});assert.equal(receiver.campaign.termination.kind,'Cancelled');
 const guidedCancelled=await runNodeAssuredGuidedCampaign(config,{maxDurationMs:0},m,metadata,DEFAULT_GUIDANCE,worker,()=>{});assert.equal(guidedCancelled.campaign.termination.kind,'Deadline');assert.equal(guidedCancelled.assurance.report.outcome,'TimedOut');
 assert.equal(guidedCancelled.assurance.report.termination,'Campaign::Deadline');assert.equal(guidedCancelled.assurance.report.budgets.max_duration_ns,'0');assert.equal(guidedCancelled.assurance.report.budgets.max_duration_ms,undefined);
 const supplied={...metadata};const mutation=await runNodeAssuredCampaign(config,{},m,supplied,worker,()=>{supplied.build='changed';});assert.equal(mutation.assurance.jobs.size,4);assert.equal(mutation.assurance.report.metadata.build,'test');
 await assert.rejects(()=>runNodeAssuredCampaign({...config,jobs:100001}, {cancellation:()=>true},m,metadata,worker,()=>{}),/expected job limit/);
 await assert.rejects(()=>assuredCampaign({...config,jobs:100001,workers:1},{cancellation:()=>true},m,metadata,()=>{throw Error('must not run');},()=>()=>{},()=>{}),/expected job limit/);
 const guidedBlocked={...worker,data:{barrier:new SharedArrayBuffer(4)}};
 const guidedFailed=await runNodeAssuredGuidedCampaign(config,{},m,metadata,DEFAULT_GUIDANCE,guidedBlocked,async()=>{await new Promise(resolve=>setTimeout(resolve,100));throw Error('guided delivery broke');});assert.equal(guidedFailed.campaign.termination.kind,'CallbackError');assert.ok(guidedFailed.assurance.jobs.size>=2);assert.equal(guidedFailed.assurance.report.outcome,'Failed');
 const encoding=await runNodeAssuredCampaign({...config,jobs:1,workers:2},{},m,metadata,{...worker,data:{failEncode:true}},()=>{});assert.equal(encoding.campaign.termination.kind,'WorkerError');assert.equal(encoding.assurance.jobs.size,1);assert.equal(encoding.campaign.finished,0);assert.equal(parseMergedAssuranceJSON(mergedAssuranceJSON(encoding.assurance)).jobs.size,1);
});
test('assurance manifest rejects invalid or duplicate requirements',()=>{
 for(const change of [m=>m.requiredProperties.push({...m.requiredProperties[0]}),m=>m.inputFamilies.push({...m.inputFamilies[0]}),m=>m.coverGoals.push({...m.coverGoals[0]}),m=>m.inputFamilies[1].coverage.reason=' ',m=>m.bounds.queue=' ',m=>m.provenance.model='',m=>m.requiredProperties[0].phase='Other',m=>m.requiredSearchOutcome='Partial']){const m=manifest();change(m);assert.throws(()=>validateAssuranceManifest(m));}
});
test('assurance catalogs follow Rust UTF-8 ordering and property id before phase',()=>{
 const m=manifest();m.requiredProperties=[{id:'z',phase:'State'},{id:'a',phase:'Transition'},{id:'\u{10000}',phase:'State'},{id:'\ue000',phase:'State'},{id:'a',phase:'State'}];m.inputFamilies=[{name:'\u{10000}',coverage:{kind:'Required'}},{name:'\ue000',coverage:{kind:'Required'}}];m.coverGoals=[{id:'\u{10000}',scope:'History'},{id:'\ue000',scope:'History'}];
 const observer=new AssuranceObserver(m,{name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'},()=>{}),report=observer.finish('NotStarted','not_started');
 assert.deepEqual(report.properties.map(p=>[p.property.id,p.property.phase]),[['a','State'],['a','Transition'],['z','State'],['\ue000','State'],['\u{10000}','State']]);assert.deepEqual(report.families.map(f=>f.name),['\ue000','\u{10000}']);assert.deepEqual(report.goals.map(g=>g.goal.id),['\ue000','\u{10000}']);validateAssuranceReport(report);
});
test('assurance JSON uses Rust wire names and exact unsigned integers',()=>{
 const m=manifest(),metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'},observer=new AssuranceObserver(m,metadata,()=>{}),report=observer.finish('NotStarted','not_started');
 report.checkedTransitions=18446744073709551615n;const json=assuranceReportJSON(report);assert.match(json,/"checked_transitions":18446744073709551615/);assert.match(json,/"termination":"NotStarted"/);assert.match(json,/"required_search_outcome":"exhausted_within_declared_bounds"/);assert.match(json,/"phase":"transition"/);
 const roundtrip=parseAssuranceReportJSON(json);assert.equal(roundtrip.checkedTransitions,18446744073709551615n);assert.equal(assuranceReportJSON(roundtrip),json);assert.equal(assuranceManifestJSON(parseAssuranceManifestJSON(assuranceManifestJSON(m))),assuranceManifestJSON(m));
 assert.throws(()=>parseAssuranceReportJSON(json.replace('"outcome":"NotStarted"','"outcome":"NotStarted","outcome":"Failed"')),/duplicate/);assert.throws(()=>parseAssuranceReportJSON(json.replace('"status":"Missing"','"status":"Passed"')),/catalog/);assert.throws(()=>parseAssuranceReportJSON(json.replace('18446744073709551615','18446744073709551616')));assert.throws(()=>parseAssuranceReportJSON(json,{maxBytes:20}));assert.throws(()=>parseAssuranceReportJSON(json,{maxEntries:1}));
 const parsed=JSON.parse(assuranceManifestJSON(m));assert.equal(parsed.input_families[1].reason,'outside model');assert.equal(parsed.cover_goals[0].scope,'history');
 const merged=mergeAssuranceReports(m,metadata,[1n,18446744073709551615n],[[1n,report]]),wire=mergedAssuranceJSON(merged);assert.match(wire,/"missing_jobs":\[18446744073709551615\]/);assert.match(wire,/"jobs":\{"1":/);
 const parsedMerged=parseMergedAssuranceJSON(wire);assert.equal(parsedMerged.jobs.get(1n).checkedTransitions,18446744073709551615n);assert.equal(parsedMerged.missingJobs[0],18446744073709551615n);assert.equal(mergedAssuranceJSON(parsedMerged),wire);
 assert.throws(()=>parseMergedAssuranceJSON(wire.replace('"checked_transitions":18446744073709551615','"checked_transitions":0')),/job evidence/);assert.throws(()=>parseMergedAssuranceJSON(wire.replace('"jobs":{','"jobs":{},"jobs":{')),/duplicate/);assert.throws(()=>parseMergedAssuranceJSON(wire,{maxEntries:1}),/bounded array|entry limit/);
 report.properties[0].status='Passed';assert.throws(()=>assuranceReportJSON(report),/catalog/);
});
test('assurance observer records checks and coverage from an actual fuzz run',()=>{
 const m=manifest();m.coverGoals[0].scope='Transition';const metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'};
 const observer=new AssuranceObserver(m,metadata,(_s,_i,_t,history,hits)=>{assert.equal(history.length,1);hits.family('input');hits.goal('reached');});
 const model={initialState:()=>0,step:(s,i)=>accepted(s+i),checkState:()=>[check('safe','passed')],checkTransition:()=>[check('safe','passed')],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,generate:()=>1,isEnabled:()=>true};
 const result=fuzz(model,{seed:1n,cases:1,maxSteps:1,maxTransitions:1n,mutationPercent:0},{},observer);assert.equal(result.transitions,1n);
 const report=observer.finish('CasesCompleted','cases_completed');assert.equal(report.checkedTransitions,1n);assert.equal(report.unclassifiedTransitions,0n);assert.deepEqual(report.properties.map(p=>p.status),['Passed','Passed']);assert.equal(report.families.find(f=>f.name==='input').status,'Observed');assert.equal(report.goals[0].status,'Observed');
 assert.equal(observer.assess('CasesCompleted','cases_completed').adequate,false);assert.throws(()=>observer.assess('GraphExhausted','failure_found'),/inconsistent/);
});
test('failed coverage discards partial hits; incomplete checks do not imply omissions',()=>{
 const m=manifest(),metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'};
 const observer=new AssuranceObserver(m,metadata,(_s,_i,_t,_h,hits)=>{hits.family('input');hits.goal('reached');throw Error('bad coverage');});
 observer.checkError('state',[],false);observer.transition(0,1,accepted(1),[check('safe','passed')],[check('safe','passed')],[1]);
 const report=observer.finish('GraphExhausted','graph_exhausted');assert.equal(report.unclassifiedTransitions,1n);assert.equal(report.families.find(f=>f.name==='input').status,'Missing');assert.equal(report.goals[0].status,'NotReachedBeforeStop');assert.match(report.callbackErrors[0],/bad coverage/);assert.equal(report.properties[0].counts.missing,0n);
 assert.equal(observer.assess('GraphExhausted','graph_exhausted').adequate,false);
});
test('assurance observation budgets are sticky and never retain partial coverage',()=>{
 const m=manifest(),metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'},pair=[check('safe','passed')];
 for(const limit of [0,1,2]){const o=new AssuranceObserver(m,metadata,(_s,_i,_t,_h,h)=>{try{h.family('input');h.goal('reached');}catch{}},{maxProperties:2,maxHitsPerTransition:limit,maxErrors:0});o.initial(0,pair);o.transition(0,1,accepted(1),pair,pair,[1]);const report=o.finish('GraphExhausted','graph_exhausted');assert.equal(report.budgets['assurance.max_hits_per_transition'],undefined);assert.equal(report.observedFamilies.includes('input'),limit===2);assert.equal(report.observedGoals.includes('reached'),limit===2);assert.equal(report.callbackErrors.length>0,limit<2);}
 const zero=new AssuranceObserver(m,metadata,()=>{},{maxProperties:0,maxErrors:0});zero.initial(0,pair);assert.match(zero.finish('NotStarted','not_started').callbackErrors[0],/incomplete/);
 let observer;observer=new AssuranceObserver(m,metadata,(_s,_i,_t,_h,h)=>{h.family('input');try{observer.initial(0,pair);}catch{}h.goal('reached');});observer.transition(0,1,accepted(1),pair,pair,[1]);const r=observer.finish('GraphExhausted','graph_exhausted');assert.deepEqual(r.observedFamilies,[]);assert.deepEqual(r.observedGoals,[]);assert.match(r.callbackErrors.join(' '),/incomplete/);
 const hostile=new Error();Object.defineProperty(hostile,'message',{get(){throw Error('bad getter');}});const o=new AssuranceObserver(m,metadata,()=>{throw hostile;});o.transition(0,1,accepted(1),pair,pair,[1]);assert.match(o.finish('Failed','ModelError').callbackErrors.join(' '),/unprintable callback error/);
 const invalid=manifest();invalid.requiredProperties[0].id='\ud800';assert.throws(()=>validateAssuranceManifest(invalid));const valid=manifest();valid.requiredProperties[0].id='😀';assert.equal(validateAssuranceManifest(valid).requiredProperties[0].id,'😀');
 const badHit=new AssuranceObserver(m,metadata,(_s,_i,_t,_h,h)=>{try{h.family('\ud800');}catch{}});badHit.transition(0,1,accepted(1),pair,pair,[1]);assert.match(badHit.finish('Failed','ModelError').callbackErrors.join(' '),/incomplete/);
 let reads=0;const changing=manifest();Object.defineProperty(changing.requiredProperties[0],'id',{get(){return ++reads===1?'safe':'x'.repeat(100000);}});const snapshot=new AssuranceObserver(changing,metadata,()=>{}, {maxManifestBytes:200});assert.equal(reads,1);assert.equal(snapshot.finish('NotStarted','not_started').manifest.requiredProperties[0].id,'safe');
 for(const operation of [v=>validateAssuranceManifest(v,{maxManifestBytes:200}),v=>assuranceManifestJSON(v,{maxManifestBytes:200})]){let count=0;const source=manifest();Object.defineProperty(source.requiredProperties[0],'id',{get(){return ++count===1?'safe':'x'.repeat(100000);}});const result=operation(source);assert.equal(count,1);assert.ok(typeof result==='string'?result.includes('safe'):result.requiredProperties[0].id==='safe');}assert.throws(()=>assuranceManifestJSON(manifest(),{maxManifestBytes:1}),/manifest byte limit/);
 let lease;const leased=new AssuranceObserver(m,metadata,(_s,_i,_t,_h,h)=>{lease=h;h.family('input');});leased.transition(0,1,accepted(1),pair,pair,[1]);const before=leased.finish('CasesCompleted','cases_completed');assert.throws(()=>lease.goal('x'.repeat(10000)),/lease expired/);assert.deepEqual(leased.finish('CasesCompleted','cases_completed'),before);
});
test('assured wrappers bind real completion, budget and errors to assurance',()=>{
 const m=manifest();m.requiredSearchOutcome='Completed';m.coverGoals[0].scope='Transition';
 const metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'};
 const model={metadata:()=>metadata,initialState:()=>0,step:()=>accepted(0),checkState:()=>[check('safe','passed')],checkTransition:()=>[check('safe','passed')],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,generate:()=>1,isEnabled:()=>true,inputs:()=>[1],equalStates:(a,b)=>a===b};
 const coverage=(_s,_i,_t,_h,hits)=>{hits.family('input');hits.goal('reached');};
 const config={seed:1n,cases:1,maxSteps:1,maxTransitions:2n,mutationPercent:0};
 const run=assuredFuzz(model,m,coverage,config);assert.equal(run.search.ok,true);assert.equal(run.assurance.outcome,'CasesCompleted');assert.equal(run.assessment.adequate,true);assert.equal(run.assurance.budgets.seed,'1');
 assert.equal(assessAssuranceReport(parseAssuranceReportJSON(assuranceReportJSON(run.assurance))).adequate,true);
 const badMetadata=structuredClone(run.assurance);badMetadata.metadata.build='\ud800';assert.equal(assessAssuranceReport(badMetadata).adequate,false);assert.throws(()=>assuranceReportJSON(badMetadata));
 const badBudget=structuredClone(run.assurance);badBudget.budgets.extra=1;assert.equal(assessAssuranceReport(badBudget).adequate,false);assert.throws(()=>assuranceReportJSON(badBudget));
 const changing=structuredClone(run.assurance);let reads=0;Object.defineProperty(changing,'outcome',{enumerable:true,get(){return ++reads===1?'CasesCompleted':'Failed';}});assert.equal(assessAssuranceReport(changing).adequate,true);assert.equal(reads,1);
 assert.equal(assessAssuranceReport(run.assurance,{maxEntries:1}).adequate,false);assert.throws(()=>assuranceReportJSON(run.assurance,{maxTextBytes:1}),/snapshot byte limit/);assert.throws(()=>assuranceReportJSON(run.assurance,{maxDepth:1}),/snapshot depth limit/);const cyclic=structuredClone(run.assurance);cyclic.extra=cyclic;assert.equal(assessAssuranceReport(cyclic).adequate,false);
 const size=new TextEncoder().encode(assuranceReportJSON(run.assurance)).length,exactStore=new AssuranceReportStore({maxRetainedReports:1,maxRetainedBytes:size});exactStore.retain(5n,run.assurance);assert.equal([...exactStore.entries()].length,1);const underStore=new AssuranceReportStore({maxRetainedReports:1,maxRetainedBytes:size-1});underStore.retain(5n,run.assurance);assert.equal([...underStore.entries()].length,0);
 const largeReport=structuredClone(run.assurance);largeReport.budgets.note='x'.repeat(10000);const tinyStore=new AssuranceReportStore({maxRetainedBytes:1});assert.doesNotThrow(()=>tinyStore.retain(5n,largeReport));assert.equal([...tinyStore.entries()].length,0);const malformed=structuredClone(run.assurance);malformed.metadata.build='\ud800';assert.throws(()=>new AssuranceReportStore().retain(5n,malformed));
 const originalStatus=run.assurance.properties[0].status;run.assurance.properties[0].status='Failed';assert.equal([...exactStore.entries()][0][1].properties[0].status,originalStatus);run.assurance.properties[0].status=originalStatus;
 const leaked=[...exactStore.entries()][0][1];leaked.properties[0].status='Failed';leaked.budgets.extra='x'.repeat(100000);assert.equal([...exactStore.entries()][0][1].properties[0].status,originalStatus);assert.equal([...exactStore.entries()][0][1].budgets.extra,undefined);
 const many=new AssuranceReportStore({maxRetainedReports:50,maxRetainedBytes:16*1024*1024}),ids=Array.from({length:40},(_,i)=>BigInt(i));for(const id of ids)many.retain(id,run.assurance);assert.throws(()=>mergeAssuranceReports(m,metadata,ids,many.entries(),{maxEntries:1000}),/snapshot entry limit/);const combined=mergeAssuranceReports(m,metadata,ids,many.entries(),many.mergeLimits());assert.equal(combined.jobs.size,40);assert.equal(combined.report.outcome,'CasesCompleted');assert.equal(assessMergedAssurance(combined,{maxBytes:1000}).adequate,false);assert.equal(assessMergedAssurance(combined,{maxBytes:1000000,maxEntries:100}).adequate,true);assert.equal(parseMergedAssuranceJSON(mergedAssuranceJSON(combined),{maxBytes:1000000,maxEntries:100}).jobs.size,40);
 const capped=assuredFuzz(model,m,coverage,config,{}, {maxHitsPerTransition:1});assert.equal(capped.assessment.adequate,false);assert.equal(capped.assurance.budgets['assurance.max_hits_per_transition'],undefined);
 const runLimits={stop:true,cancellation(){assert.equal(this,runLimits);return this.stop;}};assert.equal(assuredFuzz(model,m,coverage,config,runLimits).assurance.outcome,'Interrupted');
 const stopped=assuredFuzz(model,m,coverage,{...config,maxTransitions:0n});assert.equal(stopped.assurance.outcome,'BudgetLimited');assert.equal(stopped.assessment.adequate,false);
 const failed=assuredFuzz({...model,step:()=>{throw Error('reducer broke');}},m,coverage,config);assert.equal(failed.search.ok,false);assert.equal(failed.assurance.outcome,'Failed');assert.match(failed.assurance.callbackErrors[0],/reducer broke/);assert.equal(failed.assessment.adequate,false);
 const graph=assuredEnumerate(model,m,coverage,{maxStates:3,maxTransitions:3n,maxDepth:2});assert.equal(graph.search.ok,true);assert.equal(graph.assurance.outcome,'GraphExhausted');assert.equal(graph.assessment.adequate,true);
 const feedback={metadata:()=>({name:'features',version:1,build:'test'}),cloneFeature:x=>x,equalFeatures:(a,b)=>a===b,transitionFeatures:(_b,_i,_t,out)=>out.push('seen')};
 const guided=assuredGuidedFuzz(model,m,coverage,config,DEFAULT_GUIDANCE,feedback);assert.equal(guided.search.ok,true);assert.equal(guided.assurance.outcome,'CasesCompleted');assert.equal(guided.assurance.budgets['guidance.feedback.name'],'features');assert.equal(guided.assessment.adequate,true);
 const merged=mergeAssuranceReports(m,metadata,[1n,2n],[[1n,run.assurance],[2n,graph.assurance]]);assert.equal(merged.report.outcome,'CasesCompleted');assert.equal(merged.report.checkedTransitions,2n);assert.equal(merged.missingJobs.length,0);validateAssuranceReport(merged.report);
 assert.equal(assessAssuranceReport(merged.report).adequate,false);assert.equal(assessMergedAssurance(merged).adequate,true);const forged=structuredClone(merged);forged.report.checkedTransitions=99n;assert.equal(assessMergedAssurance(forged).adequate,false);
 const missing=mergeAssuranceReports(m,metadata,[1n,2n],[[1n,run.assurance]]);assert.deepEqual(missing.missingJobs,[2n]);assert.equal(missing.report.outcome,'Interrupted');
 assert.equal(assessMergedAssurance(missing).adequate,false);
 const tampered=structuredClone(run.assurance);tampered.properties[0].status='Failed';assert.throws(()=>validateAssuranceReport(tampered),/catalog/);
 const lied=structuredClone(run.assurance);lied.outcome='GraphExhausted';assert.throws(()=>validateAssuranceReport(lied),/inconsistent/);
 assert.throws(()=>mergeAssuranceReports(m,{...metadata,build:'different'},[1n],[[1n,run.assurance]]),/identities/);
 assert.throws(()=>mergeAssuranceReports(m,metadata,[1n,1n],[]),/duplicate/);assert.throws(()=>mergeAssuranceReports(m,metadata,[1n],[[2n,run.assurance]]),/unexpected/);
 run.assurance.properties[0].status='Failed';assert.equal(merged.jobs.get(1n).properties[0].status,'Passed');
});
test('assurance budgets and terminations use Rust src/assurance.rs spellings',async()=>{
 const m=manifest();m.requiredSearchOutcome='Completed';m.coverGoals[0].scope='Transition';
 const metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'};
 const model=()=>({metadata:()=>metadata,initialState:()=>0,step:()=>accepted(0),checkState:()=>[check('safe','passed')],checkTransition:()=>[check('safe','passed')],cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,generate:()=>1,isEnabled:()=>true});
 const coverage=(_s,_i,_t,_h,hits)=>{hits.family('input');hits.goal('reached');};
 const config={seed:1n,cases:1,maxSteps:1,maxTransitions:2n,mutationPercent:0};
 // Rust control_budgets: max_duration_ns = Duration::as_nanos().
 const timed=assuredFuzz(model(),m,coverage,config,{maxDurationMs:60000});assert.equal(timed.assurance.budgets.max_duration_ns,'60000000000');assert.equal(timed.assurance.budgets.max_duration_ms,undefined);assert.equal(timed.assurance.budgets.cancellation_enabled,'false');
 const wire=JSON.parse(assuranceReportJSON(timed.assurance));assert.equal(wire.search.termination,'CasesCompleted');assert.equal(wire.search.budgets.max_duration_ns,'60000000000');
 // Rust guided_fuzz: snake_case guidance keys, no camelCase or assurance.* keys.
 const feedback={metadata:()=>({name:'features',version:1,build:'test'}),cloneFeature:x=>x,equalFeatures:(a,b)=>a===b,transitionFeatures:(_b,_i,_t,out)=>out.push('seen')};
 const guided=assuredGuidedFuzz(model(),m,coverage,config,DEFAULT_GUIDANCE,feedback);const b=guided.assurance.budgets;
 assert.deepEqual(Object.keys(b).filter(k=>k.startsWith('guidance.')).sort(),['guidance.algorithm','guidance.feedback.build','guidance.feedback.name','guidance.feedback.version','guidance.max_corpus_entries','guidance.max_corpus_inputs','guidance.max_features','guidance.max_features_per_observation']);
 assert.equal(b['guidance.max_corpus_entries'],String(DEFAULT_GUIDANCE.maxCorpusEntries));assert.equal(b['guidance.max_features_per_observation'],String(DEFAULT_GUIDANCE.maxFeaturesPerObservation));
 assert.deepEqual(Object.keys(b).sort(),['cancellation_enabled','cases','guidance.algorithm','guidance.feedback.build','guidance.feedback.name','guidance.feedback.version','guidance.max_corpus_entries','guidance.max_corpus_inputs','guidance.max_features','guidance.max_features_per_observation','max_steps','max_transitions','mutation_percent','seed']);
 // Rust run_campaign: a panicking job retains partial observations as "Panicked".
 const campaignConfig={masterSeed:1n,firstJob:5n,jobs:2,workers:1,resultBuffer:1,stopOnFailure:false,fuzz:{seed:0n,cases:1,maxSteps:1,maxTransitions:2n,mutationPercent:0}};
 // Reducer callback throws are wrapped as ModelError; a hostile hook getter escapes as a raw TypeError (a "panic").
 const panicking=()=>Object.defineProperty(model(),'checkTransitionInto',{configurable:true,enumerable:true,get(){throw new TypeError('host bug');}});
 const outcomes=[];const run=await assuredCampaign(campaignConfig,{maxDurationMs:60000},m,metadata,panicking,()=>coverage,j=>{outcomes.push(j.outcome.kind);});
 assert.deepEqual(outcomes,['Panicked','Panicked']);const job=run.assurance.jobs.get(5n);assert.equal(job.outcome,'Failed');assert.equal(job.termination,'Panicked');assert.ok(job.callbackErrors.includes('job panicked; partial observations retained'));validateAssuranceReport(job);
 assert.equal(JSON.parse(mergedAssuranceJSON(run.assurance)).jobs['5'].search.termination,'Panicked');assert.equal(parseMergedAssuranceJSON(mergedAssuranceJSON(run.assurance)).jobs.get(5n).termination,'Panicked');
 assert.equal(run.assurance.report.outcome,'Failed');assert.equal(run.assurance.report.termination,'Campaign::JobsCompleted');assert.equal(run.assurance.report.budgets.max_duration_ns,'60000000000');
 assert.ok(Object.keys(run.assurance.report.budgets).every(k=>!k.startsWith('assurance.')));
 // A ModelError from the reducer remains a ModelError termination, not Panicked.
 const modelError=await assuredCampaign(campaignConfig,{},m,metadata,()=>({...model(),step:()=>{throw new ModelError('modeled');}}),()=>coverage,()=>{});assert.equal(modelError.assurance.jobs.get(5n).termination,'ModelError');
});
test('an unknown check status is an assurance callback error, not a search exception',()=>{
 const metadata={name:'m',model_version:1,properties_version:1,codec_version:1,build:'test'};
 const o=new AssuranceObserver(manifest(),metadata,()=>{});
 assert.doesNotThrow(()=>o.initial(0,[{id:'safe',status:'bogus',details:''}]));
 assert.doesNotThrow(()=>o.checkError('transition',[{id:'safe',status:'bogus',details:''}],false));
 const report=o.finish('GraphExhausted','graph_exhausted');
 assert.ok(report.callbackErrors.includes('invalid check status in State batch: safe'));
 assert.ok(report.callbackErrors.includes('invalid check status in Transition batch: safe'));
 const state=report.observedProperties.find(p=>p.property.phase==='State'&&p.property.id==='safe');
 assert.equal(state.counts.missing,1n);assert.equal(state.counts.passed,0n);
 validateAssuranceReport(report);
 const o2=new AssuranceObserver(manifest(),metadata,()=>{});o2.initial(0,[{id:'safe',status:'bogus',details:''}]);
 assert.equal(o2.assess('GraphExhausted','graph_exhausted').adequate,false);
});
