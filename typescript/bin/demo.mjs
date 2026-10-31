#!/usr/bin/env node
/** Runnable request-lifecycle teaching fixture. */
import {RequestModel,enumerateStates,fuzz,DEFAULT_FUZZ} from '../dist/index.js';
const usage='usage: node typescript/bin/demo.mjs <explore|fuzz> <fixed|buggy> [seed]';
const args=process.argv.slice(2);
if(args.length===1&&['--help','-h'].includes(args[0])){console.log(usage);process.exit(0);}
if(args.length<2||args.length>3||!['explore','fuzz'].includes(args[0])||!['fixed','buggy'].includes(args[1])||(args.length===3&&(args[0]!=='fuzz'||!/^\d+$/.test(args[2])))){console.error(usage);process.exit(2);}
try{
 const model=args[1]==='fixed'?RequestModel.fixed():RequestModel.buggy();
 let result;
 if(args[0]==='explore')result=enumerateStates(model);
 else{let seed=DEFAULT_FUZZ.seed;if(args.length===3){const digits=args[2].replace(/^0+/, '')||'0';if(digits.length>20||(digits.length===20&&digits>'18446744073709551615'))throw new Error('seed must be a u64');seed=BigInt(digits);}result=fuzz(model,{...DEFAULT_FUZZ,seed});}
 console.log(JSON.stringify({model:args[1],operation:args[0],result},(_key,value)=>typeof value==='bigint'?value.toString():value));
}catch(error){console.error(error instanceof Error?error.message:String(error));process.exitCode=1;}
