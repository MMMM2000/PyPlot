from pathlib import Path
import pandas as pd
from plotting.utils import origin_session

def _pad(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.reindex(range(n))

def export_and_plot_in_oc(
    grp: pd.DataFrame,
    var: str,
    tables,                     # (raw_a, raw_b, mean_a, mean_b, delta)
    out_dir: str,
    template: str = "",
    export_png: str | None = None,
):
    """
    grp: DataFrame with metadata columns you already have (composition/title/sample_end/anneal)
    var: one of ["T1","T2","dT","sum"] for label selection
    tables: tuple of 5 where each XY table has columns ['X','Y']
    """
    raw_a, raw_b, mean_a, mean_b, delta = tables
    N = max(map(len, [raw_a, raw_b, mean_a, mean_b]))

    df_out = pd.DataFrame({
        "X_raw_a": _pad(raw_a, N)["X"], "Y_raw_a": _pad(raw_a, N)["Y"],
        "X_raw_b": _pad(raw_b, N)["X"], "Y_raw_b": _pad(raw_b, N)["Y"],
        "X_mean_a": _pad(mean_a, N)["X"], "Y_mean_a": _pad(mean_a, N)["Y"],
        "X_mean_b": _pad(mean_b, N)["X"], "Y_mean_b": _pad(mean_b, N)["Y"],
    })

    comp  = str(grp.get("composition", [""])[0])
    title = str(grp.get("title", [""])[0])
    samp  = str(grp.get("sample_end", [""])[0])
    anneal= str(grp.get("anneal", [""])[0])

    ylabel_map = {"T1":"T1 (µs)", "T2":"T2 (µs)", "dT":"T2–T1 (µs)", "sum":"T1+T2 (µs)"}
    gtitle = f"{comp} {title} {samp} {anneal}".strip()
    xlabel = "Applied load (g)"
    ylabel = ylabel_map.get(var, var)

    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"stress_{comp}_{title}_{samp}_{anneal}_{var}".strip().replace(" ", "_")
    csv_path = out_dir / f"{stem}.csv"
    df_out.to_csv(csv_path, index=False)

    # Call Origin / OC
    with origin_session() as op:
        export_arg = export_png if export_png else ""
        # Ensure backslashes escaped for LabTalk
        def esc(p: Path | str) -> str:
            return str(p).replace("\\", "\\\\")
        op.lt_exec(
            rf'plot_stress_csv("{esc(csv_path)}", "{gtitle}", "{xlabel}", "{ylabel}", {float(delta)}, "{esc(template)}", "{esc(export_arg)}")'
        )

    return csv_path

