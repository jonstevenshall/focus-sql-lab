# Backlog

The next things to build.

## 1. Licensing mapping experiment

Generate synthetic software-licensing data (entitlements, subscription and support terms, part numbers, processor- or
user-based metrics, sub-capacity usage), express it in FOCUS (for example part number as `SkuId`, licence metric as
`PricingUnit`, entitlements as Contract Commitment rows), and run the spec's queries against it to show where FOCUS fits
licensing and where it strains. Licensing is a scope area FOCUS lists as under development.

## 2. Validator playground tab

A new Validator tab where a visitor runs the FinOps Foundation's
[focus_validator](https://github.com/finopsfoundation/focus_validator) 2.2.1 (released 5 August 2026) in the browser,
on the lab's own data. They change the data with SQL, run it again and see which rules change. It isn't for uploads.

### What is already proven (25 November 2026)

- The validator runs unchanged in [Pyodide](https://pyodide.org) 314.0.8 (Python 3.14). The only change is
  `tools/validator/shim.py`, which replaces `pl.read_parquet` (see below).
- `tools/validator/vrun.py` runs one file and returns plain JSON. It does what `Validator.validate()` does, without the
  outputter. The same file runs natively and in Pyodide.
- `tools/validator/test.mjs` runs it in Pyodide under Node and compares the result with a native run.
- The native reference runs used Python 3.12 with the validator's own dependencies and `pyarrow<21`. They ran on each
  source's CostAndUsage table with the lab's added columns (the manifest's `lab_columns`) dropped.
- Pyodide matched the native runs rule for rule: status, violation count and message.

| Source | Rules | Pass | Fail | Skipped | One run in Pyodide |
|---|---|---|---|---|---|
| pr7 | 1.3 | 327 | 17 | 420 | 6 s |
| pr5 | 1.4 | 288 | 7 | 435 | 5 s |
| examples | 1.4 | 213 | 82 | 435 | 5 s |
| sample (100,000 rows) | 1.2 | 99 | 38 | 441 | 12 s |

Loading the packages took 6 s and the imports 3.5 s. A first visit downloads about 60 MB from jsDelivr, which the
browser then caches:

| Download | Size |
|---|---|
| Pyodide core | 12 MB |
| polars | 18 MB |
| pyarrow | 10 MB |
| duckdb | 8.7 MB |
| pandas | 4 MB |
| numpy | 3 MB |
| pydantic | 1.8 MB |
| The rest | under 1 MB |

### Facts the build depends on

- **Rule files.** The rules are FOCUS_Spec release assets, built from `specification/requirements_model`.
  - **1.2:** `model-1.2.0.1.json`. The validator ships it.
  - **1.3:** `model-1.3.0.1.json`, from the v1.3 release.
  - **1.4:** the published `model-1.4.json` stops 2.2.1 with "Active-edge cycle detected" (validator issue #161). The
    fixed `model-1.4.0.1.json` is on the `latest-draft` prerelease. The validator picks it for "1.4" when both files are
    present.
  - **1.5:** `model-1.5-cr.json` doesn't load in 2.2.1. Some rules have no `ApplicabilityCriteria` field, which its
    pydantic model requires.
  - So the tab offers 1.2, 1.3 and 1.4, and says which file 1.4 uses.
- **Pyodide's polars build has no Parquet reader.** Text columns it reads from a file panic with "capacity overflow"
  when DuckDB registers the frame. DuckDB's register also needs pyarrow. `shim.py` reads the Parquet file with pyarrow
  and rebuilds the text columns from Python lists.
- **The validator opens `focus_validator/rules/currency_codes.csv` by a relative path**, so the worker has to change
  directory to site-packages before a run.
- **Native installs break.** pyarrow is unpinned and now resolves to a build that needs NumPy 2, but the validator pins
  NumPy 1 (#162). Pin `pyarrow<21`.
- **Open validator bugs worth showing on the tab:**
  - #187: "ChargeCategory MUST NOT be null" can never fail.
  - #164: some Correction-gated rules never check normal rows.
  - #201: rules of an omitted conditional column still run.
  - #189: date-only columns are dropped.

### Build steps

1. **Fetch and vendor.** A script (`tools/validator/fetch.sh`) fetches the following into `site/validator/`, so
   nothing is copied by hand:
   - the `focus_validator` package folder from tag v2.2.1, as a zip, with its LICENSE;
   - the sqlglot 27.29.0 wheel;
   - the three rule files;
   - `vrun.py` and `shim.py`.

   Add NOTICE.md entries for the validator (MIT), sqlglot (MIT) and the rule files (FOCUS_Spec).
2. **Write `site/validator/worker.js`, a module Web Worker.** It:
   - loads Pyodide from `cdn.jsdelivr.net/pyodide/v314.0.8/full/`;
   - loads the packages duckdb, pandas, polars, pyarrow, pydantic, jsonschema, requests and pyyaml;
   - unpacks the two zips into site-packages, writes the rules to `/rules`, changes directory and imports shim and
     vrun.

   It takes `{parquet bytes, version, criteria}` and returns vrun's JSON. Pass `rules_block_remote_download=True`, as
   vrun does.
3. **Build the page: a Validator tab button and its section.**
   - **Data:** copy the chosen source's CostAndUsage table into `validate_data`, dropping `lab_columns`, with a checkbox
     to keep them. Export it with `COPY validate_data TO 'v.parquet'` and `db.copyFileToBuffer`.
   - **Changing the data:** an SQL box that changes `validate_data`, with a few example edits (blank a column on some
     rows, drop a column, a lower-case currency) and a Reset button.
   - **Rules:** a version select, defaulting by source (pr7 1.3, pr5 1.4, examples 1.4, sample 1.2). Add checkboxes for
     the rule file's applicability criteria.
   - **Results:**
     - counts and skip reasons;
     - "changed since the last run";
     - the rules, failures first. Each shows the rule file's `MustSatisfy` text verbatim, the validator's message and
       its sample failing rows.
   - **Loading:** the first run says how much it downloads.
4. **Check the page in the browser** against native JSON for the four sources.
5. **Later:** the one template in FOCUS_Mapping_Templates ("Example Cloud", a made-up example) as a dataset, mapped by
   the lab and labelled as such.

## Open questions

- Keep the 1.0 sample mapped to FOCUS 1.5 with its 1.5-only columns empty, or drop those columns, or show it as plain 1.0?
