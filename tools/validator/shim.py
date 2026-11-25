"""The one change the page makes so focus_validator can read its data inside Pyodide.

Pyodide's polars build has no Parquet reader, and text columns it reads from a file crash it when DuckDB takes the
frame (polars-arrow "capacity overflow"). So pl.read_parquet, which the validator's Parquet loader calls, reads the
file with pyarrow instead and rebuilds the text columns from Python lists. The values are unchanged, and everything
after the read is the validator's own code.
"""
import polars as pl
import pyarrow.parquet as pq


def read_parquet(source, *args, **kwargs):
    df = pl.from_arrow(pq.read_table(source))
    return df.with_columns([pl.Series(c, df[c].to_list(), dtype=pl.String) for c, t in df.schema.items() if t == pl.String])


pl.read_parquet = read_parquet
