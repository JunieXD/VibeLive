import type { AudioSource } from "../../shared/contracts";

type BinaryEnvelopeBase = {
  sessionId: string;
  inputId: string;
  capturedAtMs: number;
  format: string;
  body: Uint8Array;
};

export type BinaryEnvelopeInput =
  | (BinaryEnvelopeBase & {
      mediaType: "audio";
      source: AudioSource;
      turnId?: string;
      systemAudioRequired?: boolean;
    })
  | (BinaryEnvelopeBase & {
      mediaType: "image";
      source?: never;
    });

const MAGIC = Buffer.from("ADVX", "ascii");
const VERSION = 2;
const FIXED_HEADER_BYTES = 25;
const MAX_TEXT_BYTES = 128;
const MAX_AUDIO_BYTES = 2_097_152;
const MAX_IMAGE_BYTES = 4_194_304;
const V3_FIXED_HEADER_BYTES = 9;
const MAX_JSON_HEADER_BYTES = 4_096;

export function formatImageMimeType(mimeType: string, changeScore: number): string {
  const normalized = mimeType.trim().toLowerCase();
  if (!["image/jpeg", "image/png", "image/webp"].includes(normalized)) {
    throw new Error("Image MIME type is not supported.");
  }
  if (!Number.isFinite(changeScore) || changeScore < 0 || changeScore > 1) {
    throw new Error("changeScore must be a finite number between zero and one.");
  }
  return `${normalized};advx-change-score=${changeScore.toFixed(6)}`;
}

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
  output.writeUInt8(sourceByte(input), 6);
  output.writeUInt16BE(sessionId.length, 7);
  output.writeUInt16BE(inputId.length, 9);
  output.writeBigUInt64BE(BigInt(input.capturedAtMs), 11);
  output.writeUInt16BE(format.length, 19);
  output.writeUInt32BE(body.length, 21);

  let cursor = FIXED_HEADER_BYTES;
  for (const part of [sessionId, inputId, format, body]) {
    part.copy(output, cursor);
    cursor += part.length;
  }
  return output;
}

export function encodeAtomicBinaryEnvelope(input: BinaryEnvelopeInput): Uint8Array {
  const body = Buffer.from(input.body.buffer, input.body.byteOffset, input.body.byteLength);
  const bodyLimit = input.mediaType === "audio" ? MAX_AUDIO_BYTES : MAX_IMAGE_BYTES;
  if (body.length === 0 || body.length > bodyLimit) {
    throw new Error(`Binary ${input.mediaType} body is outside the allowed size.`);
  }
  if (!Number.isSafeInteger(input.capturedAtMs) || input.capturedAtMs < 0) {
    throw new Error("capturedAtMs must be a non-negative safe integer.");
  }
  for (const [field, value] of [
    ["sessionId", input.sessionId],
    ["inputId", input.inputId],
    ["format", input.format]
  ] as const) {
    encodeText(value, field);
  }
  if (input.mediaType === "audio") {
    if (input.turnId === undefined) {
      throw new Error("Atomic audio needs a turnId.");
    }
    encodeText(input.turnId, "turnId");
  }
  if (
    input.mediaType === "audio" &&
    input.systemAudioRequired &&
    (input.source !== "microphone" || input.turnId === undefined)
  ) {
    throw new Error("System audio requirements need a microphone turnId.");
  }

  const header = Buffer.from(JSON.stringify({
    media_type: input.mediaType,
    ...(input.mediaType === "audio" ? { source: input.source } : {}),
    session_id: input.sessionId,
    input_id: input.inputId,
    captured_at_ms: input.capturedAtMs,
    format: input.format,
    body_length: body.length,
    ...(input.mediaType === "audio"
      ? {
          turn_id: input.turnId,
          system_audio_required: input.systemAudioRequired ?? false
        }
      : {})
  }), "utf8");
  if (header.length > MAX_JSON_HEADER_BYTES) {
    throw new Error(`Binary JSON header exceeds ${MAX_JSON_HEADER_BYTES} UTF-8 bytes.`);
  }

  const output = Buffer.allocUnsafe(V3_FIXED_HEADER_BYTES + header.length + body.length);
  MAGIC.copy(output, 0);
  output.writeUInt8(3, 4);
  output.writeUInt32BE(header.length, 5);
  header.copy(output, V3_FIXED_HEADER_BYTES);
  body.copy(output, V3_FIXED_HEADER_BYTES + header.length);
  return output;
}

function sourceByte(input: BinaryEnvelopeInput): number {
  if (input.mediaType === "image") return 0;
  if (input.source === "microphone") return 1;
  if (input.source === "system_audio") return 2;
  throw new Error("Audio source is not supported.");
}

function encodeText(value: string, field: string): Buffer {
  if (!value || value.includes("\0")) throw new Error(`${field} must be non-empty text.`);
  const encoded = Buffer.from(value, "utf8");
  if (encoded.length > MAX_TEXT_BYTES) {
    throw new Error(`${field} exceeds ${MAX_TEXT_BYTES} UTF-8 bytes.`);
  }
  return encoded;
}
