import { afterEach, expect, it, vi } from "vitest";
import { requestKey } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("creates version 4 request keys on HTTP LAN without randomUUID", () => {
  vi.stubGlobal("crypto", {
    getRandomValues: (bytes: Uint8Array) => bytes.fill(0xab),
  });
  expect(requestKey()).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});
