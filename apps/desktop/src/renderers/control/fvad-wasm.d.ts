declare module '@echogarden/fvad-wasm' {
  type FvadWasmModule = {
    HEAP16: Int16Array
    _fvad_new: () => number
    _fvad_free: (handle: number) => void
    _fvad_set_mode: (handle: number, mode: number) => number
    _fvad_set_sample_rate: (handle: number, sampleRate: number) => number
    _fvad_process: (handle: number, audioPointer: number, frameLength: number) => number
    _malloc: (size: number) => number
    _free: (pointer: number) => void
  }

  const createFvad: () => Promise<FvadWasmModule>

  export default createFvad
}
