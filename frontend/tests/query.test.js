import assert from "node:assert/strict";
import { test } from "node:test";
import { submitQuery } from "../src/query.js";

test("query uses the relative API path and preserves the backend response", async (t) => {
  const result = { answer: "An answer", evidence: [], routing: { complexity: "simple", retrieval_depth: 0, activated_modules: ["fact_lookup"] } };
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/query");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.deepEqual(JSON.parse(options.body), { query: "A question" });
    return Response.json(result);
  });
  assert.deepEqual(await submitQuery("A question"), result);
});

test("API failures expose useful messages", async (t) => {
  for (const [body, expected] of [
    [{ detail: "retrieval failed" }, /retrieval failed/],
    [{ detail: [{ msg: "query must not be empty" }] }, /query must not be empty/],
    [{ error: { message: "Generation unavailable" } }, /Generation unavailable/],
    [{}, /HTTP 500/],
  ]) {
    t.mock.method(globalThis, "fetch", async () => Response.json(body, { status: 500 }));
    await assert.rejects(submitQuery("Question"), expected);
    t.mock.restoreAll();
  }
});

test("network failures and malformed responses are handled", async (t) => {
  t.mock.method(globalThis, "fetch", async () => { throw new TypeError("fetch failed"); });
  await assert.rejects(submitQuery("Question"), /Could not reach the API/);
  t.mock.restoreAll();
  for (const [response, expected] of [
    [new Response("bad gateway", { status: 502 }), /HTTP 502/],
    [new Response("not JSON"), /invalid response/],
    [Response.json(null), /invalid response/],
  ]) {
    t.mock.method(globalThis, "fetch", async () => response);
    await assert.rejects(submitQuery("Question"), expected);
    t.mock.restoreAll();
  }
});
