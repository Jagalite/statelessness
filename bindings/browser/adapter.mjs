// No package dependencies. All application state and reducer logic stay in JS.
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const u32 = n => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, n, true); return b; };
const join = (...parts) => {
  const bytes = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.length; }
  return bytes;
};
const blob = bytes => join(u32(bytes.length), bytes);
const string = s => blob(encoder.encode(s));
const integer = bytes => {
  if (bytes.length !== 4) throw new Error("invalid counter value");
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0, true);
};

export async function createCounter(wasmBytes) {
  let api;
  let callbackError = "";
  const read = (ptr, len) => new Uint8Array(api.memory.buffer, ptr, len).slice();
  const allocate = bytes => {
    const handle = api.stateless_buffer_new(bytes.length);
    if (!handle) throw new Error("Wasm buffer allocation failed");
    const pointer = api.stateless_buffer_data(handle);
    new Uint8Array(api.memory.buffer, pointer, bytes.length).set(bytes);
    return { handle, pointer, length: bytes.length };
  };
  const dispatch = (context, operation, statePointer, stateLength, inputPointer, inputLength, response) => {
    try {
      const state = read(statePointer, stateLength);
      const input = read(inputPointer, inputLength);
      let bytes;
      switch (operation) {
        case 0: bytes = u32(0); break;
        case 1: {
          const next = integer(state) + integer(input) + (context ? 1 : 0);
          if (next > 0xffffffff) throw new Error("counter overflow");
          bytes = join(new Uint8Array([0]), string(""), blob(u32(next)), u32(1), blob(u32(next)));
          break;
        }
        case 2: {
          const failed = integer(state) > 2;
          bytes = join(u32(1), string("counter_bound"), new Uint8Array([failed ? 1 : 0]), string(failed ? "counter exceeded 2" : ""));
          break;
        }
        case 3: bytes = u32(0); break;
        case 4: integer(state); bytes = join(u32(1), blob(u32(1))); break;
        default: throw new Error(`unknown operation ${operation}`);
      }
      const temporary = allocate(bytes);
      try { return api.stateless_buffer_assign(response, temporary.pointer, temporary.length); }
      finally { api.stateless_buffer_free(temporary.handle); }
    } catch (error) {
      callbackError = String(error);
      return 11; // Do not throw through Wasm.
    }
  };
  const result = await WebAssembly.instantiate(wasmBytes, { stateless_host: { dispatch } });
  api = result.instance.exports;
  if (api.stateless_abi_version() !== 1) throw new Error("unsupported ABI");

  function errorText() {
    const output = api.stateless_buffer_new(0);
    try {
      api.stateless_last_error(output);
      return decoder.decode(read(api.stateless_buffer_data(output), api.stateless_buffer_len(output))) || callbackError;
    } finally { api.stateless_buffer_free(output); }
  }
  function withModel(changed, operation) {
    const name = allocate(encoder.encode("javascript-counter"));
    const build = allocate(encoder.encode("javascript-counter-fixture-v1"));
    const out = allocate(u32(0));
    let model = 0;
    try {
      const status = api.stateless_wasm_model_new(changed ? 1 : 0, name.pointer, name.length, build.pointer, build.length, 1, 1, 1, out.pointer);
      if (status !== 0) throw new Error(`create model: ${status} ${errorText()}`);
      model = new DataView(api.memory.buffer).getUint32(out.pointer, true);
      return operation(model);
    } finally {
      if (model) api.stateless_model_free(model);
      for (const buffer of [name, build, out]) api.stateless_buffer_free(buffer.handle);
    }
  }
  return {
    enumerate(maxDepth = 10) {
      return withModel(false, model => {
        const output = api.stateless_buffer_new(0);
        const report = api.stateless_buffer_new(0);
        try {
          const status = api.stateless_enumerate(model, 20, 100n, maxDepth, report, output);
          if (status !== 0 && status !== 1) throw new Error(`enumerate: ${status} ${errorText()}`);
          return {
            status,
            report: decoder.decode(read(api.stateless_buffer_data(report), api.stateless_buffer_len(report))),
            artifact: read(api.stateless_buffer_data(output), api.stateless_buffer_len(output)),
          };
        } finally { api.stateless_buffer_free(report); api.stateless_buffer_free(output); }
      });
    },
    record() {
      return withModel(false, model => {
        const batch = allocate(join(u32(3), blob(u32(1)), blob(u32(1)), blob(u32(1))));
        const output = api.stateless_buffer_new(0);
        try {
          const status = api.stateless_record(model, batch.pointer, batch.length, 10, output);
          if (status !== 1) throw new Error(`record: ${status} ${errorText()}`);
          return read(api.stateless_buffer_data(output), api.stateless_buffer_len(output));
        } finally { api.stateless_buffer_free(batch.handle); api.stateless_buffer_free(output); }
      });
    },
    replay(artifact, changed = false) {
      return withModel(changed, model => {
        const input = allocate(artifact);
        try {
          const status = api.stateless_replay(model, input.pointer, input.length);
          return { status, detail: status === 1 ? "Exact replay matched; counter_bound failure reproduced" : errorText() };
        } finally { api.stateless_buffer_free(input.handle); }
      });
    },
  };
}
