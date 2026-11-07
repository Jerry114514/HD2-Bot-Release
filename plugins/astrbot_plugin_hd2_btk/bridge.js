/* JSON-only bridge between the AstrBot Python plugin and hd2-btk-sdk v0.14.1. */
const sdk = require("./sdk/index.js");

function fail(error) {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: error instanceof Error ? error.message : String(error),
  }));
  process.exitCode = 2;
}

try {
  const action = process.argv[2];
  const payload = JSON.parse(process.argv[3] || "{}");
  let result;
  if (action === "compute") result = sdk.compute(payload);
  else if (action === "scanStuck") result = sdk.scanStuck(payload);
  else if (action === "demolitionCatalog") result = { structures: sdk.structures, blasts: sdk.blasts };
  else if (action === "demolition") result = sdk.demolition(payload.blast, payload.structure);
  else if (action === "demolitionAt") result = sdk.demolitionAt(payload.blast, payload.distance);
  else throw new Error("未知 BTK 操作：" + action);
  process.stdout.write(JSON.stringify({ ok: true, result }));
} catch (error) {
  fail(error);
}
