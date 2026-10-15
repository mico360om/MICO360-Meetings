// A local stand-in for MICO360 Connect (OpenAI-compatible surface) for the emulator flows.
//   node e2e/fakeConnect.mjs [port]      (the emulator reaches it at http://10.0.2.2:<port>/v1)
// The first chat request is answered with a 307 to another port, to record whether
// the phone's HTTP stack follows it and whether the Authorization header goes along.
import { createServer } from "node:http";
import { writeFileSync } from "node:fs";

const PORT = Number(process.argv[2] ?? 8787);
const OTHER = PORT + 1;
const LOG = new URL("./fake-connect-log.json", import.meta.url);
const log = { requests: [], redirect: { sent: false, followed: false, authorizationArrived: null } };
const save = () => writeFileSync(LOG, JSON.stringify(log, null, 2));

export const MINUTES = [
  "# Meeting Minutes", "**Meeting Title:** E2E Budget review", "**Date and Time:** 15 Oct 2026", "**Attendees:** Ali, Sara",
  "", "## Decisions Made", "- Approve the budget", "", "## Action Items",
  "| Task | Owner | Deadline | Status |", "| --- | --- | --- | --- |", "| Send budget report | Ali | 2030-01-15 | Pending |",
].join("\n");

const json = (res, status, body, headers = {}) => {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(JSON.stringify(body));
};

createServer((req, res) => {
  let body = "";
  req.on("data", (d) => { body += d; });
  req.on("end", () => {
    log.requests.push({ method: req.method, url: req.url, auth: req.headers.authorization ? "present" : "absent",
      idem: req.headers["idempotency-key"] ?? "", corr: req.headers["x-mico-correlation-id"] ?? "", bytes: body.length });
    save();
    if (req.url === "/__reset") {          // a fresh run: send the redirect again, forget old requests
      log.requests = []; log.redirect = { sent: false, followed: false, authorizationArrived: null }; save();
      return json(res, 200, { ok: true });
    }
    if (req.method === "GET" && req.url === "/v1/models") return json(res, 200, { object: "list", data: [{ id: "llama3.1:latest", object: "model" }] });
    if (req.method === "POST" && req.url === "/v1/chat/completions") {
      if (!log.redirect.sent) {
        log.redirect.sent = true; save();
        res.writeHead(307, { Location: `http://10.0.2.2:${OTHER}/v1/chat/completions` });
        return res.end();
      }
      return json(res, 200, { id: "chatcmpl-e2e", object: "chat.completion", model: "llama3.1:latest",
        choices: [{ index: 0, message: { role: "assistant", content: MINUTES }, finish_reason: "stop" }] });
    }
    json(res, 404, { error: { message: "Not found", type: "not_found" } });
  });
}).listen(PORT, () => console.log(`fake Connect on :${PORT}`));

createServer((req, res) => {
  log.redirect.followed = true;
  log.redirect.authorizationArrived = !!req.headers.authorization;
  save();
  json(res, 200, { choices: [{ message: { content: "# Redirected" } }] });
}).listen(OTHER, () => console.log(`redirect target on :${OTHER}`));
save();
