"""Run focus_validator on one data file and return its results as plain JSON.

Same steps as Validator.validate() (load rules and data, run the plan, apply the result overrides) without the
outputter. Used natively for the parity check and, unchanged, inside Pyodide by the page.
"""
import json
import logging
import math
import sys

logging.disable(logging.CRITICAL)

from focus_validator.validator import Validator  # noqa: E402


def _plain(v):
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if hasattr(v, "item") and not hasattr(v, "columns"):
        try:
            return _plain(v.item())
        except Exception:
            return str(v)
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    if hasattr(v, "to_dict") and hasattr(v, "columns"):  # a pandas or polars frame of sample rows
        try:
            rows = v.to_dict("records") if hasattr(v, "iloc") else v.to_dicts()
        except Exception:
            return str(v)
        return [_plain(r) for r in rows[:5]]
    return str(v)


def run(data_path, version, rules_dir, criteria=None, dataset="CostAndUsage"):
    v = Validator(
        data_filename=data_path,
        output_destination=None,
        output_type="console",
        rule_set_path=rules_dir,
        rules_version=version,
        focus_dataset=dataset,
        rules_block_remote_download=True,
        applicability_criteria=criteria or None,
        show_violations=True,
    )
    v.load()
    res = v.spec_rules.validate(v.focus_data, show_violations=True, data_filename=data_path,
                                data_row_count=v.data_row_count)
    out = {}
    for rid, entry in res.by_rule_id.items():
        d = entry.get("details") or {}
        status = "skip" if d.get("skipped") else ("pass" if entry.get("ok") else "fail")
        r = {"status": status, "violations": _plain(d.get("violations")), "message": _plain(d.get("message")),
             "reason": _plain(d.get("reason"))}
        if d.get("failure_cases") is not None:
            r["sample"] = _plain(d["failure_cases"])
        if d.get("missing_columns"):
            r["missing"] = _plain(d["missing_columns"])
        if d.get("failed_child_ids"):
            r["failed_children"] = _plain(d["failed_child_ids"])
        out[rid] = {k: x for k, x in r.items() if x is not None}
    return {"version": res.rules_version, "model": res.model_version, "rows": res.data_row_count,
            "columns": list(v.focus_data.columns) if v.focus_data is not None else [], "results": out}


if __name__ == "__main__":
    data, version, rules = sys.argv[1:4]
    crit = sys.argv[4] if len(sys.argv) > 4 else None
    print(json.dumps(run(data, version, rules, crit)))
