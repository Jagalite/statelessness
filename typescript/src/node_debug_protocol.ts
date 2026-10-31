/** Node-only stdio adapter for the framed debugger protocol. */
// @ts-ignore Node declarations are not required by the browser package.
import process from 'node:process';
import {serve,type ProtocolSession} from './debug_protocol.js';

export interface NodeProtocolOutput {
    write(bytes: Uint8Array, callback: (error?: Error | null) => void): boolean;
    on(event: 'error', callback: (error: Error) => void): unknown;
    removeListener(event: 'error', callback: (error: Error) => void): unknown;
}

/**
 * Serve framed requests on Node streams. One complete response write finishes
 * before the next request is handled. This function does not close either
 * stream or cancel the model; transport errors reject after disconnecting.
 * Output errors while idle are observed at the next response or input EOF;
 * interrupting an idle input stream remains the caller's responsibility.
 */
export async function serveNodeStdio<S,I,O>(session:ProtocolSession<S,I,O>,input:AsyncIterable<Uint8Array>=process.stdin,output:NodeProtocolOutput=process.stdout):Promise<void>{
    let failure:Error|undefined;
    let pending:((error:Error)=>void)|undefined;
    const onError=(error:Error)=>{failure=error;pending?.(error);};
    output.on('error',onError);
    try{
        await serve(session,input,frame=>new Promise<void>((resolve,reject)=>{
            if(failure){reject(failure);return;}
            pending=reject;
            try{output.write(frame,error=>{if(error){failure=error;reject(error);}else resolve();pending=undefined;});}
            catch(error){pending=undefined;reject(error);}
        }));
    }finally{
        // Writable errors can be emitted immediately after a failed write's
        // callback; leave the listener in place until the next event-loop turn.
        await new Promise<void>(resolve=>setTimeout(resolve,0));
        output.removeListener('error',onError);
    }
    if(failure)throw failure;
}
