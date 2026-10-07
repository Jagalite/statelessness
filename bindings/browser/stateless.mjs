// ABI 1 adapter for application-owned canonical byte models. No dependencies.
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const MAX = 64 * 1024 * 1024;
export const u32 = n => {
  if (!Number.isSafeInteger(n) || n < 0 || n > 0xffffffff) throw new Error("invalid u32");
  const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, n, true); return b;
};
const concatenate = parts => {
  let length = 0;
  for (const part of parts) {
    bytes(part);
    if (part.length > MAX - length) throw new Error("packet exceeds 64 MiB");
    length += part.length;
  }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) { result.set(part, offset); offset += part.length; }
  return result;
};
export const join = (...parts) => concatenate(parts);
const bytes = value => {
  if (!(value instanceof Uint8Array) || value.length > MAX) throw new Error("expected bounded Uint8Array");
  return value;
};
const blob = value => join(u32(bytes(value).length), value);
const string = value => {
  if (typeof value !== "string") throw new Error("expected string");
  return blob(encoder.encode(value));
};
const batch = values => {
  if (!Array.isArray(values) || values.length > 1_000_000) throw new Error("invalid batch");
  // Preflight the entire packet before copying payloads. In particular, never
  // spread a model-sized list into function arguments (engine-dependent cap).
  let length = 4;
  for (const value of values) {
    bytes(value);
    if (value.length + 4 > MAX - length) throw new Error("packet exceeds 64 MiB");
    length += value.length + 4;
  }
  const packet = new Uint8Array(length);
  const view = new DataView(packet.buffer);
  view.setUint32(0, values.length, true);
  let offset = 4;
  for (const value of values) {
    view.setUint32(offset, value.length, true);
    packet.set(value, offset + 4);
    offset += value.length + 4;
  }
  return packet;
};
function checks(values) {
  if (!Array.isArray(values) || values.length > 1_000_000) throw new Error("invalid checks");
  const parts = [u32(values.length)];
  let length = 4;
  for (const check of values) {
    const status = ["passed", "failed", "skipped"].indexOf(check.status);
    if (!check.id || status < 0) throw new Error("invalid check");
    const part = join(string(check.id), new Uint8Array([status]), string(check.details ?? ""));
    if (part.length > MAX - length) throw new Error("packet exceeds 64 MiB");
    length += part.length;
    parts.push(part);
  }
  return concatenate(parts);
}
function transition(value) {
  const tag = ["accepted", "rejected", "ignored"].indexOf(value.disposition ?? "accepted");
  if (tag < 0) throw new Error("invalid disposition");
  return join(new Uint8Array([tag]), string(value.reason ?? ""), blob(value.state), batch(value.outputs ?? []));
}
function observed(packet) {
  let offset = 0;
  const take = n => {
    if (n > packet.length - offset) throw new Error("truncated observation");
    const result = packet.slice(offset, offset + n); offset += n; return result;
  };
  const number = () => new DataView(take(4).buffer).getUint32(0, true);
  const value = () => take(number());
  const input = value();
  const disposition = ["accepted", "rejected", "ignored"][take(1)[0]];
  if (!disposition) throw new Error("invalid disposition");
  const reason = decoder.decode(value());
  const state = value();
  const count = number();
  if (count > 1_000_000) throw new Error("too many outputs");
  const outputs = Array.from({ length: count }, value);
  if (offset !== packet.length) throw new Error("trailing observation bytes");
  return { input, transition: { disposition, reason, state, outputs } };
}

/** Callbacks are synchronous and deterministic. Mutable logical state belongs in
 * canonical bytes, never hidden in the callback closure. No real effects here.
 * Each operation creates/frees a fresh model; returned arrays are owned copies. */
export async function createModel(wasmBytes, model) {
  let api;
  let busy = false;
  let callbackError = "";
  const metadata = { ...model.metadata };
  if (typeof metadata.name !== "string" || !metadata.name || typeof metadata.build !== "string" || !metadata.build) {
    throw new Error("model name and build must be nonempty strings");
  }
  for (const key of ["modelVersion", "propertiesVersion", "codecVersion"]) u32(metadata[key]);
  const read = (ptr, len) => new Uint8Array(api.memory.buffer, ptr, len).slice();
  const allocate = value => {
    bytes(value);
    const handle = api.stateless_buffer_new(value.length);
    if (!handle) throw new Error("Wasm buffer allocation failed");
    const pointer = api.stateless_buffer_data(handle);
    new Uint8Array(api.memory.buffer, pointer, value.length).set(value);
    return { handle, pointer, length: value.length };
  };
  const dispatch = (_context, operation, sp, sl, ip, il, response) => {
    try {
      const state = read(sp, sl), input = read(ip, il);
      let result;
      switch (operation) {
        case 0: result = bytes(model.initial()); break;
        case 1: result = transition(model.step(state, input)); break;
        case 2: result = checks(model.checkState(state)); break;
        case 3: {
          const observation = observed(input);
          result = checks(model.checkTransition?.(state, observation.input, observation.transition) ?? []);
          break;
        }
        case 4: result = batch(model.inputs(state)); break;
        default: throw new Error("unknown callback operation");
      }
      const temporary = allocate(result);
      try { return api.stateless_buffer_assign(response, temporary.pointer, temporary.length); }
      finally { api.stateless_buffer_free(temporary.handle); }
    } catch (error) { callbackError = String(error); return 11; }
  };
  const result = await WebAssembly.instantiate(wasmBytes, { stateless_host: { dispatch } });
  api = (result.instance ?? result).exports;
  if (api.stateless_abi_version() !== 1) throw new Error("unsupported ABI");
  function errorText() {
    const output = allocate(new Uint8Array());
    try {
      api.stateless_last_error(output.handle);
      const detail = decoder.decode(read(api.stateless_buffer_data(output.handle), api.stateless_buffer_len(output.handle)));
      return callbackError ? `${detail}: ${callbackError}` : detail;
    } finally { api.stateless_buffer_free(output.handle); }
  }
  function withModel(operation) {
    if (busy) throw new Error("model operations cannot reenter callbacks");
    busy = true; callbackError = "";
    const buffers = [];
    let handle = 0;
    const owned = value => { const b = allocate(value); buffers.push(b); return b; };
    try {
      const name = owned(encoder.encode(metadata.name));
      const build = owned(encoder.encode(metadata.build));
      const out = owned(u32(0));
      const status = api.stateless_wasm_model_new(0, name.pointer, name.length, build.pointer, build.length,
        metadata.modelVersion, metadata.propertiesVersion, metadata.codecVersion, out.pointer);
      if (status !== 0) throw new Error(`create model: ${status} ${errorText()}`);
      handle = new DataView(api.memory.buffer).getUint32(out.pointer, true);
      return operation(handle, owned);
    } finally {
      if (handle) api.stateless_model_free(handle);
      for (const b of buffers) api.stateless_buffer_free(b.handle);
      busy = false;
    }
  }
  const output = buffer => read(api.stateless_buffer_data(buffer.handle), api.stateless_buffer_len(buffer.handle));
  return {
    record(inputs, maxSteps = inputs.length) {
      u32(maxSteps);
      return withModel((handle, owned) => {
        const input = owned(batch(inputs)), artifact = owned(new Uint8Array());
        const status = api.stateless_record(handle, input.pointer, input.length, maxSteps, artifact.handle);
        if (status !== 0 && status !== 1) throw new Error(`record: ${status} ${errorText()}`);
        return { status, artifact: output(artifact) };
      });
    },
    replay(artifact) {
      return withModel((handle, owned) => {
        const input = owned(artifact);
        const status = api.stateless_replay(handle, input.pointer, input.length);
        return { status, detail: status === 0 ? "Exact replay" : status === 1 ? "Exact replay; property failure reproduced" : errorText() };
      });
    },
    enumerate({ maxStates = 100_000, maxTransitions = 1_000_000n, maxDepth = 100 } = {}) {
      u32(maxStates); u32(maxDepth);
      if (typeof maxTransitions !== "bigint" || maxTransitions < 0n || maxTransitions > 0xffffffffffffffffn) throw new Error("invalid u64 transition limit");
      return withModel((handle, owned) => {
        const artifact = owned(new Uint8Array()), report = owned(new Uint8Array());
        const status = api.stateless_enumerate(handle, maxStates, maxTransitions, maxDepth, report.handle, artifact.handle);
        if (status !== 0 && status !== 1) throw new Error(`enumerate: ${status} ${errorText()}`);
        return { status, artifact: output(artifact), report: decoder.decode(output(report)) };
      });
    },
  };
}
