import { describe, expect, it } from "vitest";
import { createSseParser, readSseStream, type SseMessage } from "./sse";

function collect(chunks: string[]): SseMessage[] {
  const out: SseMessage[] = [];
  const parser = createSseParser((m) => out.push(m));
  for (const chunk of chunks) parser.push(chunk);
  parser.end();
  return out;
}

function splitEvery(text: string, size: number): string[] {
  const parts: string[] = [];
  for (let i = 0; i < text.length; i += size) parts.push(text.slice(i, i + size));
  return parts;
}

const STREAM =
  'event: start\ndata: {"trace_id":"t1","conversation_id":"c1","message_id":"m1"}\n\n' +
  ": keep-alive\n\n" +
  'event: step\ndata: {"seq":1,"step":"planning","title":"Понимаю вопрос"}\n\n' +
  'event: final\ndata: {"summary":"Выручка выросла на 3,1 %"}\n\n';

describe("createSseParser", () => {
  it("parses whole events", () => {
    const events = collect([STREAM]);
    expect(events.map((e) => e.event)).toEqual(["start", "step", "final"]);
    expect(JSON.parse(events[1].data)).toMatchObject({ seq: 1, title: "Понимаю вопрос" });
  });

  it("gives identical result for every chunk size (events split across chunks)", () => {
    const expected = collect([STREAM]);
    for (let size = 1; size <= 40; size += 1) {
      expect(collect(splitEvery(STREAM, size))).toEqual(expected);
    }
  });

  it("handles CRLF with \\r and \\n in different chunks", () => {
    const events = collect(["event: step\r", "\ndata: {\"seq\":2}\r", "\n\r", "\n"]);
    expect(events).toEqual([{ event: "step", data: '{"seq":2}', id: null }]);
  });

  it("handles bare CR line endings", () => {
    const events = collect(["event: final\rdata: x\r\r"]);
    expect(events).toEqual([{ event: "final", data: "x", id: null }]);
  });

  it("joins multi-line data and defaults event name to message", () => {
    const events = collect(["data: line1\ndata: line2\n\n"]);
    expect(events).toEqual([{ event: "message", data: "line1\nline2", id: null }]);
  });

  it("strips only one leading space and keeps values without space", () => {
    const events = collect(["event:step\ndata:  two spaces\n\n"]);
    expect(events).toEqual([{ event: "step", data: " two spaces", id: null }]);
  });

  it("dispatches a trailing event without final blank line on end()", () => {
    const events = collect(['event: final\ndata: {"ok":true}']);
    expect(events).toEqual([{ event: "final", data: '{"ok":true}', id: null }]);
  });

  it("ignores events without data and strips BOM", () => {
    const events = collect(["﻿event: ping\n\n", "event: step\ndata: 1\n\n"]);
    expect(events).toEqual([{ event: "step", data: "1", id: null }]);
  });
});

describe("readSseStream", () => {
  it("decodes multi-byte UTF-8 split between byte chunks", async () => {
    const bytes = new TextEncoder().encode(STREAM);
    const byteChunks: Uint8Array[] = [];
    // размер 1 байт гарантированно режет кириллицу (2 байта на символ)
    for (let i = 0; i < bytes.length; i += 1) byteChunks.push(bytes.slice(i, i + 1));
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of byteChunks) controller.enqueue(chunk);
        controller.close();
      },
    });
    const out: SseMessage[] = [];
    await readSseStream(body, (m) => out.push(m));
    expect(out).toEqual(collect([STREAM]));
    expect(JSON.parse(out[2].data)).toEqual({ summary: "Выручка выросла на 3,1 %" });
  });
});
