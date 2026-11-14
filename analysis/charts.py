"""ビュー 1 のグラフ（altair）：コース図、距離×速度、距離×タイム差。

区間の連動は Vega-Lite の選択だけで行い、Python には戻さない。

- `s_brush`：距離のグラフ（速度・タイム差のどちら）で横にドラッグした `s` の範囲。
  コース図では、基準線の点のうち `s` がこの範囲にある点を色付けする。
- `map_brush`：コース図でドラッグした矩形。基準線の点のうち矩形の中にある点の `s` の最小〜最大を、
  距離のグラフに帯として描く。矩形がコースの離れた 2 か所にかかると、帯はその間を全部含む。
- 距離のグラフは層（layer）の重ね合わせで作る。`corners`（`ingest.corners.load_corners`）を渡すと、
  コーナーの区間を灰色の帯と id で距離のグラフに、頂点の位置に id をコース図に描く。
"""

import altair as alt
import numpy as np
import polars as pl

from ingest.corners import Corner
from ingest.track import Refline

HIGHLIGHT = "#d62728"
CORNER_GRAY = "#808080"
MAP_WIDTH = 420
PLOT_WIDTH = 760
PLOT_HEIGHT = 220
S_TICK = 500.0


def line_frame(line: Refline) -> pl.DataFrame:
    return pl.DataFrame({"s": line.s, "x": line.x, "y": line.y})


def corner_frame(corners: list[Corner], line: Refline) -> pl.DataFrame:
    """コーナーの区間と、頂点の基準線上の位置。"""
    apex = np.array([c.s_apex for c in corners], dtype=float)
    return pl.DataFrame({
        "id": [c.id for c in corners],
        "name": [c.name for c in corners],
        "s_start": [c.s_start for c in corners],
        "s_end": [c.s_end for c in corners],
        "x": np.interp(apex, line.s, line.x),
        "y": np.interp(apex, line.s, line.y),
    }, schema_overrides={"s_start": pl.Float64, "s_end": pl.Float64})


def view1_chart(
    tel: pl.DataFrame, line: Refline, ref: str, corners: list[Corner] | None = None
) -> alt.HConcatChart:
    """`tel` は `analysis.data.time_delta` の返り値。`ref` は基準ラップの `label`。"""
    s_brush = alt.selection_interval(name="s_brush", encodings=["x"])
    map_brush = alt.selection_interval(name="map_brush", encodings=["x", "y"])
    ln = line_frame(line)
    laps = tel.select(
        "label",
        "source",
        "s",
        speed_kmh=pl.col("speed") * 3.6,
        dt="dt",
    )
    s_scale = alt.Scale(domain=[0, float(np.floor(line.length))], nice=False)
    color = alt.Color("label:N", title="周", legend=alt.Legend(orient="bottom", columns=1))
    dash = alt.StrokeDash("source:N", title="ソース", legend=alt.Legend(orient="bottom"))

    def bands(brush: alt.Parameter) -> alt.Chart:
        """`brush` で選んだ基準線の点の `s` の範囲を、距離のグラフの帯にする。"""
        return (
            alt.Chart(ln)
            .transform_filter({"param": brush.name, "empty": False})
            .transform_aggregate(s0="min(s)", s1="max(s)")
            .mark_rect(color=HIGHLIGHT, opacity=0.15)
            .encode(x=alt.X("s0:Q", title="s [m]", scale=s_scale), x2="s1:Q")
        )

    cf = corner_frame(corners, line) if corners else None

    def corner_layers(label: bool) -> list[alt.Chart]:
        if cf is None:
            return []
        band = (
            alt.Chart(cf)
            .mark_rect(color=CORNER_GRAY, opacity=0.08)
            .encode(
                x=alt.X("s_start:Q", title="s [m]", scale=s_scale),
                x2="s_end:Q",
                tooltip=["id:N", "name:N", "s_start:Q", "s_end:Q"],
            )
        )
        if not label:
            return [band]
        text = (
            alt.Chart(cf)
            .transform_calculate(s_mid="(datum.s_start + datum.s_end) / 2")
            .mark_text(baseline="top", dy=2, fontSize=10, color=CORNER_GRAY)
            .encode(x=alt.X("s_mid:Q", scale=s_scale), y=alt.value(0), text="id:N")
        )
        return [band, text]

    def panel(field: str, title: str, label_corners: bool) -> alt.LayerChart:
        lines = (
            alt.Chart(laps)
            .mark_line(clip=True)
            .encode(
                x=alt.X("s:Q", title="s [m]", scale=s_scale),
                y=alt.Y(f"{field}:Q", title=title, scale=alt.Scale(zero=field == "dt")),
                color=color,
                strokeDash=dash,
                strokeWidth=alt.when(alt.datum.label == ref)
                .then(alt.value(2.0))
                .otherwise(alt.value(1.2)),
                tooltip=["label:N", "s:Q", alt.Tooltip(f"{field}:Q", format=".2f")],
            )
            .add_params(s_brush)
            .properties(name=f"lines_{field}")
        )
        return alt.layer(
            *corner_layers(label_corners), bands(map_brush), bands(s_brush), lines
        ).properties(width=PLOT_WIDTH, height=PLOT_HEIGHT)

    speed = panel("speed_kmh", "速度 [km/h]", label_corners=True)
    delta = panel("dt", f"Δt [s]（基準：{ref}）", label_corners=False)

    return alt.hconcat(
        course_map(ln, s_brush, map_brush, cf),
        alt.vconcat(speed, delta),
    ).resolve_scale(color="shared", strokeDash="shared")


def course_map(
    ln: pl.DataFrame,
    s_brush: alt.Parameter,
    map_brush: alt.Parameter,
    cf: pl.DataFrame | None = None,
) -> alt.LayerChart:
    """基準線のコース図。x 東・y 北を同じ縮尺で描く。"""
    x0, x1 = ln["x"].min(), ln["x"].max()
    y0, y1 = ln["y"].min(), ln["y"].max()
    pad = 0.05 * max(x1 - x0, y1 - y0)
    xd, yd = [x0 - pad, x1 + pad], [y0 - pad, y1 + pad]
    height = MAP_WIDTH * (yd[1] - yd[0]) / (xd[1] - xd[0])
    x = alt.X("x:Q", scale=alt.Scale(domain=xd, nice=False), axis=None)
    y = alt.Y("y:Q", scale=alt.Scale(domain=yd, nice=False), axis=None)

    points = (
        alt.Chart(ln)
        .mark_circle(size=6, opacity=1)
        .encode(
            x=x,
            y=y,
            color=alt.when(s_brush, empty=False)
            .then(alt.value(HIGHLIGHT))
            .when(map_brush, empty=False)
            .then(alt.value(HIGHLIGHT))
            .otherwise(alt.value("#b0b0b0")),
            tooltip=[alt.Tooltip("s:Q", format=".0f")],
        )
        .add_params(map_brush)
        .properties(name="map_points")
    )
    ticks = ln.filter(pl.col("s") % S_TICK == 0)
    tick_marks = alt.Chart(ticks).mark_point(shape="cross", color="black").encode(x=x, y=y)
    tick_text = (
        alt.Chart(ticks)
        .mark_text(align="left", dx=6, dy=-6, fontSize=10)
        .encode(x=x, y=y, text=alt.Text("s:Q", format=".0f"))
    )
    layers = [points, tick_marks, tick_text]
    if cf is not None:
        layers.append(
            alt.Chart(cf)
            .mark_text(fontSize=11, fontWeight="bold", color=CORNER_GRAY, dx=10, dy=10)
            .encode(x=x, y=y, text="id:N", tooltip=["id:N", "name:N"])
        )
    return alt.layer(*layers).properties(
        width=MAP_WIDTH, height=height, title=f"コース図（数字は s [m]、{S_TICK:.0f} m ごと）"
    )
