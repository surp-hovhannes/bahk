// Local static demo plus a separate-origin API fixture for real browser CORS tests.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const fixture = require("./calendar.json");
const root = path.resolve(__dirname, "../..");

http.createServer((request, response) => {
  const pathname = new URL(request.url, "http://localhost").pathname;
  if (pathname === "/blank") {
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    response.end('<!doctype html><html lang="en"><title>Widget test</title><main></main></html>');
    return;
  }
  const file = path.resolve(root, "." + (pathname === "/" ? "/docs/examples/widgets.html" : pathname));
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    response.writeHead(404).end();
    return;
  }
  response.setHeader("Content-Type", file.endsWith(".js") ? "text/javascript" : "text/html; charset=utf-8");
  fs.createReadStream(file).pipe(response);
}).listen(8765, "127.0.0.1");

http.createServer((request, response) => {
  const url = new URL(request.url, "http://localhost");
  const church = url.searchParams.get("church_id");
  // Church 2 deliberately omits CORS headers to exercise browser enforcement.
  if (church !== "2") response.setHeader("Access-Control-Allow-Origin", "http://127.0.0.1:8765");
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Cache-Control", "no-store");
  const data = structuredClone(fixture);
  data.date = url.searchParams.get("date");
  data.church.id = Number(church);
  response.end(JSON.stringify(data));
}).listen(8766, "127.0.0.1");
