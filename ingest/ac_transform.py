"""AC のワールド座標から局所座標（ENU）への変換（SPEC「基準線と座標変換」）。

使い方（推定して `track/<course>/ac_transform.toml` を書く）：
`python -m ingest.ac_transform <acraw> <course>`

- AC のワールド座標は左手系で y が上。水平面を `(x, -z)` にとると、x 東・y 北の ENU と同じ右手系になる。
  2026-11-14 のキャプチャで確かめた：`(x, z)` のままでは岡山の周回が反時計回り（実車は時計回り）になり、
  `(x, -z)` で求めた横G が `physics.accG[0]` と符号まで一致する。
- 変換は相似変換 `p_enu = scale * R(rotation) @ (x, -z) + (tx, ty)`。鏡映は含めない。
- 推定は ICP（最近傍点の対応づけと Umeyama 法の繰り返し）。初期の回転角を 15° おきに試し、
  残差の RMS が最小のものを採る。
- 合わせる先は基準線（`track/<course>/refline.csv`）。
- スケールが 1 から `SCALE_TOLERANCE` 以上外れたら警告する。
"""

import sys
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

TRACK_DIR = Path(__file__).resolve().parents[1] / "track"
SCALE_TOLERANCE = 0.02


@dataclass(frozen=True)
class Similarity:
    scale: float
    rotation_deg: float
    tx: float
    ty: float

    def apply(self, xy: np.ndarray) -> np.ndarray:
        th = np.radians(self.rotation_deg)
        r = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        return self.scale * np.asarray(xy, dtype=np.float64) @ r.T + [self.tx, self.ty]


def ac_plane(coords: np.ndarray) -> np.ndarray:
    """`graphics.carCoordinates`（N×3）を右手系の水平面 `(x, -z)`（N×2）にする。"""
    c = np.asarray(coords, dtype=np.float64)
    return np.c_[c[:, 0], -c[:, 2]]


def _umeyama(p: np.ndarray, q: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    mp, mq = p.mean(0), q.mean(0)
    pc, qc = p - mp, q - mq
    u, s, vt = np.linalg.svd(qc.T @ pc / len(p))
    d = np.eye(2)
    if np.linalg.det(u @ vt) < 0:
        d[1, 1] = -1
    r = u @ d @ vt
    scale = np.trace(np.diag(s) @ d) / pc.var(0).sum()
    return scale, r, mq - scale * r @ mp


def _densify(q: np.ndarray, step: float) -> np.ndarray:
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(q, axis=0).T))]
    s = np.arange(0, d[-1], step)
    return np.c_[np.interp(s, d, q[:, 0]), np.interp(s, d, q[:, 1])]


def fit_similarity(
    src: np.ndarray, target: np.ndarray, *, step: float = 0.5, iterations: int = 60
) -> tuple[Similarity, np.ndarray]:
    """`src`（AC の水平面、N×2）を折れ線 `target`（ENU、M×2）に合わせる。

    返り値は変換と、変換後の各点から `target` までの距離 [m]。
    """
    src = np.asarray(src, dtype=np.float64)
    q = _densify(np.asarray(target, dtype=np.float64), step)
    tree = cKDTree(q)
    best = None
    for th0 in np.radians(np.arange(0, 360, 15)):
        r = np.array([[np.cos(th0), -np.sin(th0)], [np.sin(th0), np.cos(th0)]])
        scale, tr = 1.0, q.mean(0) - r @ src.mean(0)
        for _ in range(iterations):
            _, idx = tree.query(scale * src @ r.T + tr)
            scale, r, tr = _umeyama(src, q[idx])
        dist, _ = tree.query(scale * src @ r.T + tr)
        rms = float(np.sqrt(np.mean(dist**2)))
        if best is None or rms < best[0]:
            rot = float(np.degrees(np.arctan2(r[1, 0], r[0, 0])))
            best = (rms, Similarity(float(scale), rot, float(tr[0]), float(tr[1])), dist)
    _, sim, dist = best
    if abs(sim.scale - 1) >= SCALE_TOLERANCE:
        warnings.warn(f"推定したスケール {sim.scale:.4f} が 1 から外れている", stacklevel=2)
    return sim, dist


def load_transform(
    ac_track: str, ac_config: str, track_dir: Path = TRACK_DIR
) -> tuple[Path, Similarity]:
    """AC の track / trackConfiguration に一致するコースのディレクトリと変換を返す。"""
    found = []
    for p in sorted(track_dir.glob("*/ac_transform.toml")):
        conf = tomllib.loads(p.read_text(encoding="utf-8"))
        if (conf["ac_track"], conf["ac_config"]) == (ac_track, ac_config):
            found.append((p, conf))
    if len(found) != 1:
        raise ValueError(
            f"AC の {ac_track}/{ac_config} に一致する ac_transform.toml が1つではない: "
            f"{[str(f[0]) for f in found]}（python -m ingest.ac_transform で推定する）"
        )
    path, conf = found[0]
    return path.parent, Similarity(conf["scale"], conf["rotation_deg"], conf["tx"], conf["ty"])


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python -m ingest.ac_transform <acraw> <course>")
    from ingest import acraw
    from ingest.track import load_refline

    acpath, course = Path(argv[0]), argv[1]
    cap = acraw.read_acraw(acpath)
    closed = acraw.closed_laps(cap, acraw.lap_starts(cap))
    if closed.size == 0:
        raise SystemExit(f"{acpath}: 閉じた周がない")
    src = ac_plane(cap.graphics["carCoordinates"][closed])[::30]
    line = load_refline(TRACK_DIR / course)
    sim, dist = fit_similarity(src, np.r_[line.xy, line.xy[:1]])

    p50, p95, pmax = np.percentile(dist, [50, 95, 100])
    text = f"""# AC のワールド座標の水平面 (x, -z) から局所座標（ENU）への相似変換。ingest.ac_transform が書く。
# p_enu = scale * R(rotation_deg) @ (x, -z) + (tx, ty)
ac_track = "{cap.static_str('track')}"
ac_config = "{cap.static_str('trackConfiguration')}"
scale = {sim.scale:.6f}
rotation_deg = {sim.rotation_deg:.4f}
tx = {sim.tx:.3f}
ty = {sim.ty:.3f}

# 推定の出どころ。合わせた先は基準線（refline.csv）
fit_source = "{acpath.name}"
fit_residual_m = {{ median = {p50:.2f}, p95 = {p95:.2f}, max = {pmax:.2f} }}
"""
    out = TRACK_DIR / course / "ac_transform.toml"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1:])
