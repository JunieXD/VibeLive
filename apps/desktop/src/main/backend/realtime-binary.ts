export type BinaryMediaType = "audio" | "image";

export type BinaryEnvelopeInput = {
  mediaType: BinaryMediaType;
  sessionId: string;
  inputId: string;
  capturedAtMs: number;
  format: string;
  body: Uint8Array;
};

const MAGIC = Buffer.from("ADVX", "ascii");
const VERSION = 1;
const FIXED_HEADER_BYTES = 24;
const MAX_TEXT_BYTES = 128;
const MAX_AUDIO_BYTES = 1_048_576;
const MAX_IMAGE_BYTES = 4_194_304;

export function encodeBinaryEnvelope(input: BinaryEnvelopeInput): Uint8Array {
  const sessionId = encodeText(input.sessionId, "sessionId");
  const inputId = encodeText(input.inputId, "inputId");
  const format = encodeText(input.format, "format");
  const body = Buffer.from(input.body.buffer, input.body.byteOffset, input.body.byteLength);
  const bodyLimit = input.mediaType === "audio" ? MAX_AUDIO_BYTES : MAX_IMAGE_BYTES;
  if (body.length === 0 || body.length > bodyLimit) {
    throw new Error(`Binary ${input.mediaType} body is outside the allowed size.`);
  }
  if (!Number.isSafeInteger(input.capturedAtMs) || input.capturedAtMs < 0) {
    throw new Error("capturedAtMs must be a non-negative safe integer.");
  }

  const output = Buffer.allocUnsafe(
    FIXED_HEADER_BYTES + sessionId.length + inputId.length + format.length + body.length
  );
  MAGIC.copy(output, 0);
  output.writeUInt8(VERSION, 4);
  output.writeUInt8(input.mediaType === "audio" ? 1 : 2, 5);
  output.writeUInt16BE(sessionId.length, 6);
  output.writeUInt16BE(inputId.length, 8);
  output.writeBigUInt64BE(BigInt(input.capturedAtMs), 10);
  output.writeUInt16BE(format.length, 18);
  output.writeUInt32BE(body.length, 20);

  let cursor = FIXED_HEADER_BYTES;
  for (const part of [sessionId, inputId, format, body]) {
    part.copy(output, cursor);
    cursor += part.length;
  }
  return output;
}

function encodeText(value: string, field: string): Buffer {
  if (!value || value.includes("\0")) throw new Error(`${field} must be non-empty text.`);
  const encoded = Buffer.from(value, "utf8");
  if (encoded.length > MAX_TEXT_BYTES) {
    throw new Error(`${field} exceeds ${MAX_TEXT_BYTES} UTF-8 bytes.`);
  }
  return encoded;
}
