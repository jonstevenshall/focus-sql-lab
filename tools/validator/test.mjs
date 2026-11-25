// Run focus_validator 2.2.1 inside Pyodide (Node) on one Parquet file and compare with a native run's JSON.
//   VFILES=<folder> node test.mjs <data.parquet> <version> <native.json>   (after: npm install pyodide@314.0.8)
import { loadPyodide } from "pyodide";
import fs from "node:fs";

const S = process.env.VFILES; // a folder holding wheels/ (focus_validator and sqlglot), rules/ (model-*.json), vrun.py and shim.py
const [data, ver, ref] = process.argv.slice(2);
const bytes = (p) => new Uint8Array(fs.readFileSync(p));
const t0 = Date.now();
const py = await loadPyodide();
await py.loadPackage(["duckdb", "pandas", "polars", "pyarrow", "pydantic", "jsonschema", "requests", "pyyaml"], { messageCallback: () => {} });
const site = py.runPython("import site; site.getsitepackages()[0]");
for (const w of fs.readdirSync(`${S}/wheels`)) py.unpackArchive(bytes(`${S}/wheels/${w}`), "zip", { extractDir: site });
py.FS.mkdirTree("/rules"); py.FS.mkdirTree("/data");
for (const f of fs.readdirSync(`${S}/rules`)) py.FS.writeFile(`/rules/${f}`, bytes(`${S}/rules/${f}`));
py.FS.writeFile("/data/d.parquet", bytes(data));
for (const f of ["vrun.py", "shim.py"]) py.FS.writeFile(`/${f}`, bytes(`${S}/${f}`));
const t1 = Date.now();
py.runPython(`import os, sys; os.chdir(${JSON.stringify(site)}); sys.path.insert(0, "/"); import shim, vrun, json`);
const t2 = Date.now();
const run = () => py.runPython(`json.dumps(vrun.run("/data/d.parquet", "${ver}", "/rules"))`);
const out = run();
const t3 = Date.now();
run();
const t4 = Date.now();
const got = JSON.parse(out), want = JSON.parse(fs.readFileSync(ref, "utf8"));
const ids = new Set([...Object.keys(got.results), ...Object.keys(want.results)]);
const diffs = [];
let msgDiff = 0;
for (const id of ids) {
  const a = got.results[id], b = want.results[id];
  if (!a || !b || a.status !== b.status || a.violations !== b.violations) diffs.push([id, b && `${b.status}/${b.violations}`, a && `${a.status}/${a.violations}`, a?.message?.slice(0, 120), b?.message?.slice(0, 120)]);
  else if (a.message !== b.message || a.reason !== b.reason) msgDiff++;
}
const count = (r) => Object.values(r).reduce((c, x) => ((c[x.status] = (c[x.status] || 0) + 1), c), {});
console.log(`${data.split("/").pop()} ${ver}: packages ${(t1 - t0) / 1000}s, imports ${(t2 - t1) / 1000}s, first run ${(t3 - t2) / 1000}s, second run ${(t4 - t3) / 1000}s; pyodide ${JSON.stringify(count(got.results))} native ${JSON.stringify(count(want.results))}; ${diffs.length} differ, ${msgDiff} messages differ`);
for (const d of diffs.slice(0, 15)) console.log("  ", JSON.stringify(d));
