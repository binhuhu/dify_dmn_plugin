import http from "node:http";
import { fork } from "node:child_process";
import { createHash, timingSafeEqual } from "node:crypto";
import { fileURLToPath } from "node:url";
import { parseTree, getNodeValue } from "jsonc-parser";
import { ENGINE, LIMITS, failure } from "./evaluate.js";

const workerPath = fileURLToPath(new URL("./worker.js", import.meta.url));
function configInteger(value, fallback, min, max) {
  const n = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(n) || n < min || n > max)
    throw Error("Invalid server limit configuration");
  return n;
}
export function parseStrictJson(text) {
  const errors = [];
  const tree = parseTree(text, errors, {
    disallowComments: true,
    allowTrailingComma: false,
  });
  if (errors.length || !tree) throw Error("Invalid JSON");
  function visit(node, depth = 0) {
    if (depth > LIMITS.depth + 10) throw Error("JSON nesting limit");
    if (node.type === "object") {
      const keys = new Set();
      for (const p of node.children || []) {
        const k = p.children[0].value;
        if (
          keys.has(k) ||
          ["__proto__", "constructor", "prototype"].includes(k)
        )
          throw Error("Duplicate or reserved JSON key");
        keys.add(k);
        visit(p.children[1], depth + 1);
      }
    } else for (const child of node.children || []) visit(child, depth + 1);
  }
  visit(tree);
  return getNodeValue(tree);
}
function operationFailure(mode, code, message, request) {
  const error = { code, message };
  if (mode === "query")
    return {
      schema_version: "query-capability.candidate.v1",
      capability_id: request?.capability_id ?? null,
      status: "FAILED",
      outcome: code === "EVALUATION_TIMEOUT" ? "QUERY_TIMEOUT" : "ERROR",
      outputs: null,
      error,
      provenance: null,
    };
  if (mode === "plan")
    return {
      schema_version: "query-dmn-plan-result.candidate.v1",
      plan_id: request?.plan?.plan_id ?? null,
      plan_version: request?.plan?.version ?? null,
      plan_sha256: null,
      flow: request?.plan?.flow ?? null,
      status: "FAILED",
      release_status: "CANDIDATE",
      execution_mode: "ADVISORY_ONLY",
      mock_queries: true,
      production_compatibility: "UNVERIFIED",
      outputs: null,
      phases: [],
      steps: [],
      error,
      evidence_gaps: [],
      engine: ENGINE,
    };
  return failure(
    code,
    message,
    typeof request?.decision_id === "string" ? request.decision_id : null,
  );
}
export function createServer(options = {}) {
  const token = options.token ?? process.env.DMN_API_TOKEN;
  if (typeof token !== "string" || token.length < 32)
    throw Error("DMN_API_TOKEN must be at least 32 characters");
  const expected = createHash("sha256").update(`Bearer ${token}`).digest();
  const timeoutMs = configInteger(
    options.timeoutMs ?? process.env.DMN_TIMEOUT_MS,
    5000,
    50,
    15000,
  );
  const concurrency = configInteger(
    options.concurrency ?? process.env.DMN_CONCURRENCY,
    2,
    1,
    8,
  );
  const active = new Set();
  let shuttingDown = false;
  function send(res, status, body) {
    if (res.destroyed || res.writableEnded) return;
    const json = JSON.stringify(body);
    res.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": Buffer.byteLength(json),
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(json);
  }
  const server = http.createServer(async (req, res) => {
    const supplied = createHash("sha256")
      .update(req.headers.authorization || "")
      .digest();
    if (!timingSafeEqual(expected, supplied)) {
      req.resume();
      return send(
        res,
        401,
        failure("UNAUTHORIZED", "Valid bearer authentication required"),
      );
    }
    if (req.url === "/health" && req.method === "GET")
      return send(res, shuttingDown ? 503 : 200, {
        status: shuttingDown ? "stopping" : "ok",
        protocol_version: "1.0",
        engine: ENGINE,
      });
    if (
      !["/evaluate", "/query", "/execute_plan"].includes(req.url) ||
      req.method !== "POST"
    ) {
      req.resume();
      return send(res, 404, failure("NOT_FOUND", "Unknown endpoint"));
    }
    if (shuttingDown || active.size >= concurrency) {
      req.resume();
      return send(
        res,
        503,
        failure("OVERLOADED", "Evaluation capacity is occupied; retry later"),
      );
    }
    if (
      !/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(
        req.headers["content-type"] || "",
      )
    ) {
      req.resume();
      return send(
        res,
        415,
        failure("INVALID_INPUT", "Content-Type must be application/json"),
      );
    }
    // Reserve capacity before reading the body: slow uploads cannot exceed the bound.
    const slot = { child: null };
    active.add(slot);
    let finished = false,
      timer;
    const release = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (slot.child && !slot.child.killed) slot.child.kill("SIGKILL");
      active.delete(slot);
    };
    res.once("close", release);
    timer = setTimeout(() => {
      send(
        res,
        408,
        failure("REQUEST_TIMEOUT", "Request upload deadline exceeded"),
      );
      release();
      req.destroy();
    }, 5000);
    const chunks = [];
    let total = 0,
      request;
    try {
      for await (const chunk of req) {
        total += chunk.length;
        if (total > LIMITS.request) {
          send(
            res,
            413,
            failure("INPUT_TOO_LARGE", "Request exceeds size limit"),
          );
          release();
          req.destroy();
          return;
        }
        chunks.push(chunk);
      }
      if (finished) return;
      request = parseStrictJson(
        new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)),
      );
    } catch {
      send(
        res,
        400,
        failure(
          "INVALID_JSON",
          "Body must be strict UTF-8 JSON without duplicate keys",
        ),
      );
      release();
      return;
    }
    clearTimeout(timer);
    const mode =
      req.url === "/execute_plan"
        ? "plan"
        : req.url === "/query"
          ? "query"
          : "single";
    const failed = (code, message) =>
      operationFailure(mode, code, message, request);
    try {
      // A fresh OS process guarantees a CPU-bound FEEL loop can actually be killed.
      // Credentials and parent environment are never inherited by the evaluator.
      slot.child = fork(options.workerPath || workerPath, [], {
        execArgv: ["--max-old-space-size=128", "--disable-proto=throw"],
        env: { TZ: "UTC", LANG: "C.UTF-8" },
        stdio: ["ignore", "ignore", "ignore", "ipc"],
      });
      timer = setTimeout(() => {
        send(
          res,
          200,
          failed(
            "EVALUATION_TIMEOUT",
            "Evaluation exceeded hard wall-clock limit",
          ),
        );
        release();
      }, timeoutMs);
      slot.child.once("message", (result) => {
        if (finished) return;
        try {
          if (Buffer.byteLength(JSON.stringify(result)) > LIMITS.response)
            send(
              res,
              200,
              failed("RESULT_TOO_LARGE", "Result exceeds response limit"),
            );
          else send(res, 200, result);
        } catch {
          send(
            res,
            200,
            failed(
              "INVALID_RESULT",
              "Evaluation did not produce a JSON result",
            ),
          );
        }
        release();
      });
      slot.child.once("error", () => {
        send(res, 200, failed("WORKER_FAILED", "Could not start evaluator"));
        release();
      });
      slot.child.once("exit", () => {
        if (!finished) {
          send(
            res,
            200,
            failed("WORKER_FAILED", "Evaluator exited without a result"),
          );
          release();
        }
      });
      slot.child.send({ mode, request });
    } catch {
      send(res, 200, failed("WORKER_FAILED", "Could not start evaluator"));
      release();
    }
  });
  server.requestTimeout = 6000;
  server.headersTimeout = 6000;
  server.keepAliveTimeout = 1000;
  server.shutdown = () => {
    shuttingDown = true;
    for (const slot of active) slot.child?.kill("SIGKILL");
    server.close();
  };
  return server;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const server = createServer();
  const port = configInteger(process.env.PORT, 8787, 1, 65535);
  server.listen(port, process.env.DMN_HOST || "127.0.0.1", () =>
    console.log(`DMN service ready on port ${port}`),
  );
  for (const signal of ["SIGTERM", "SIGINT"])
    process.on(signal, () => server.shutdown());
}
