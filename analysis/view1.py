"""ビュー 1：コース図と、距離×速度・距離×タイム差の重ね合わせ（SPEC「ビュー」）。

起動：`uv run marimo edit analysis/view1.py`（閲覧だけなら `marimo run`）
"""

import marimo

__generated_with = "0.25.2"

app = marimo.App(width="full")


@app.cell
def _():
    import warnings
    from pathlib import Path

    import marimo as mo

    from analysis import data
    from analysis.charts import view1_chart
    from ingest.corners import load_corners
    from ingest.track import TRACK_DIR, load_refline

    # 速度とタイム差のグラフで同じ選択 s_brush を共有するときに altair が出す通知。共有は意図どおり
    warnings.filterwarnings("ignore", message="Automatically deduplicated selection parameter")
    return Path, TRACK_DIR, data, load_corners, load_refline, mo, view1_chart


@app.cell
def _(TRACK_DIR, data, mo):
    courses = sorted(p.parent.name for p in TRACK_DIR.glob("*/refline.csv"))
    data_dir_ui = mo.ui.text(
        value=str(data.default_data_dir()), label="データの場所", full_width=True
    )
    course_ui = mo.ui.dropdown(courses, value=courses[0] if courses else None, label="コース")
    show_invalid_ui = mo.ui.checkbox(label="無効な周（lap_valid = false）も候補に出す")
    mo.vstack(
        [
            mo.md(
                "# ビュー 1：コース図と距離のグラフ\n"
                "`<データの場所>/<source>/<session_id>/telemetry.parquet` を読む。"
                "既定は環境変数 `LAPDIFF_DATA`、なければリポジトリ直下の `data/`。"
            ),
            data_dir_ui,
            course_ui,
            show_invalid_ui,
        ]
    )
    return course_ui, data_dir_ui, show_invalid_ui


@app.cell
def _(Path, TRACK_DIR, course_ui, data, data_dir_ui, load_corners, load_refline):
    if course_ui.value is None:
        raise FileNotFoundError(f"{TRACK_DIR}/<course>/refline.csv がない")
    line = load_refline(TRACK_DIR / course_ui.value)
    # corners.toml はコースによってはまだない。ないときは帯を描かず、その旨を表示する
    has_corners = (TRACK_DIR / course_ui.value / "corners.toml").exists()
    corners = load_corners(TRACK_DIR / course_ui.value) if has_corners else None
    con = data.open_db(Path(data_dir_ui.value).expanduser())
    laps = data.lap_list(con)
    data.check_length(laps, line.length)
    return con, corners, laps, line


@app.cell
def _(data, laps, mo, show_invalid_ui):
    import polars as pl

    # 一覧に出す周・基準ラップの候補・既定で重ねる周の決め方は web view（web/src/data.ts の
    # shownLaps・refCandidates・defaultSelection）にも写してある。変えたらそちらも合わせる
    shown = laps if show_invalid_ui.value else laps.filter("lap_valid")
    mo.stop(
        shown.height == 0,
        mo.callout(
            mo.md("有効な周（lap_valid = true）が 1 つもない。無効な周を候補に出すと選べる"),
            kind="warn",
        ),
    )
    ref_options = shown.filter("complete")["label"].to_list()
    default_ref = data.default_ref(laps)
    laps_ui = mo.ui.multiselect(
        shown["label"].to_list(),
        value=shown.filter("lap_valid")["label"].to_list(),
        label="重ねる周",
        full_width=True,
    )
    ref_ui = mo.ui.dropdown(
        ref_options,
        value=default_ref if default_ref in ref_options else None,
        label="基準ラップ",
    )
    mo.vstack(
        [
            mo.md("## 周の一覧"),
            mo.ui.table(
                shown.select(
                    "label", "lap_valid", "complete", "s_min", "s_max",
                    pl.col("t_end").round(3),
                ),
                selection=None,
                page_size=20,
            ),
            mo.md(
                "`t_end` は周の最後の格子点（s が最大）での t。"
                "`complete` が false の周（計測線の前後だけの断片など）はタイム差を出さない。"
            ),
            laps_ui,
            ref_ui,
        ]
    )
    return laps_ui, pl, ref_ui


@app.cell
def _(con, corners, data, laps_ui, line, mo, ref_ui, view1_chart):
    mo.stop(
        ref_ui.value is None,
        mo.callout(
            mo.md("基準ラップを選ぶ（実車の有効で欠けのない周がないため、既定値がない）"),
            kind="warn",
        ),
    )
    labels = sorted(set(laps_ui.value) | {ref_ui.value})
    tel = data.time_delta(data.load_laps(con, labels), ref_ui.value)
    mo.vstack(
        [
            mo.md(
                "## コース図と距離のグラフ\n"
                "コース図で矩形をドラッグすると、基準線のその範囲の s が距離のグラフに帯で出る。"
                "距離のグラフで横にドラッグすると、その s の範囲がコース図で赤くなる。"
                "ダブルクリックで選択を消す。"
                + (
                    "灰色の帯と T1 などの記号はコーナーの区間（corners.toml）。"
                    if corners
                    else "このコースには corners.toml がないので、コーナーの区間は描かない。"
                )
            ),
            # mo.ui.altair_chart で包むと、周の行は Arrow のファイルとして別に送られる。
            # 素の altair のままだと仕様に JSON で埋め込まれ、3 周で出力の上限（数 MB）を超える。
            # 区間の選択は Vega-Lite の中で連動させるので、marimo の選択は使わない
            mo.ui.altair_chart(
                view1_chart(tel, line, ref_ui.value, corners),
                chart_selection=False,
                legend_selection=False,
            ),
        ]
    )
    return (tel,)


@app.cell
def _(data, laps, mo, pl, tel):
    final = data.final_delta(tel).join(
        laps.select("label", "t_end"), on="label", how="left"
    )
    mo.vstack(
        [
            mo.md("## 最後の s での Δt"),
            mo.ui.table(
                final.select("label", "s", pl.col("t_end").round(3), pl.col("dt").round(3)),
                selection=None,
            ),
        ]
    )


if __name__ == "__main__":
    app.run()
