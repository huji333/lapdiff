"""コースの端：空中写真から、基準線の各 `s` での路面の左右の端を取り出す。

使い方：`uv run python -m ingest.edges <course>`（`track/<course>/edges.csv` を書く）

- 元の画像は国土地理院の地理院タイル「全国最新写真（シームレス）」（seamlessphoto）のズーム 18
  （約 0.5 m/px）。基準線の範囲に `MARGIN` m を足した範囲のタイルを `CACHE_DIR` に取得する。
  キャッシュにあるタイルは取り直さない。書き出した CSV を git に入れ、`analysis.export` はネットワークを使わない。
- 座標：`track/<course>/origin.toml` の原点の ENU（`ingest.racechrono.to_enu`）を緯度・経度に戻し、
  Web メルカトルのタイルの画素に置く。写真と GPS の位置合わせの誤差で、端は ±1〜2 m ずれうる。
- 路面の判定（`road_mask`）：明るさ（RGB の平均）が `LUM_MAX` 未満で、青み（B - R）が `BLUE_MIN` 以上の画素。
  アスファルトは暗く青みがかり、芝・土・白線・縁石は外れる。この判定で取れるのはアスファルトの端（白線の内側）。
- 白線と縁石（`paint_masks`・`rim`）：アスファルトの端から外へ、白・赤・灰色の画素が続く所を読む
  （それ以外が `GAP_MAX` m を超えて続けば止める）。
  - 白：明るさ `WHITE_LUM` 以上、彩度（RGB の最大 - 最小）`WHITE_SAT` 以下、R - B が `WHITE_RB` 以下（黄みの土を外す）。
  - 灰色：白の条件のうち明るさだけを満たさない画素。くすんだアスファルトや白線のぼけた縁で、越えて読む。
    okayama の T8 の出口（s = 2965〜2980）ではアスファルトの外側 1 m ほどが明るい灰色に写り、
    灰色を越えないと白線に届かなかった。
  - 赤：R - G が `RED_RG` 以上、R - B が `RED_RB` 以上、明るさ `RED_LUM` 未満。写真の縁石の赤は色が薄く
    （T5 で R - G = 11〜16）、外側の舗装した退避帯（R - G = 6〜10、明るさ 208〜220）と分けるため明るさに上限を置く。
  - 走路の限界は白線の外側なので、白線を路面（`left_m`・`right_m`）に含める。白線は、アスファルトの端から
    `LINE_SEARCH` m 以内で始まる最初の白の並びで、幅は `LINE_MAX` m まで。白線の外の舗装（T1 の退避帯など）も
    白に判定されるので、`LINE_MAX` で止める。多くのコーナーの縁石は幅 1 m ほどの白と灰色の縞に写り、
    白線と区別できない。そうした縁石は白線として路面に入り、縁石とはみなさない。
  - 縁石：同じ並びの中で、白線の外から `KERB_MAX` m 以内の最も外の赤の画素までを縁石とする。
    写真は約 0.5 m/px でぼけており、縁石の紅白の縞は分かれず薄い赤の帯に写る（T5 で幅約 1.5〜2 m）。
    - くすんだアスファルト：アスファルトの端から外へ暗い灰色（灰色のうち明るさ `LUM_MAX` 未満）が途切れずに続く所は、
    白線を探す起点をその外側までずらす（`LINE_SEARCH` はそこから測る）。okayama の T8 の頂点の内側
    （s = 2895〜2917）では、白線の手前 2〜3 m のアスファルトが青みを失って（B - R = -1〜7）路面の判定から外れ、
    白線が `LINE_SEARCH` の外になって、路面の端が 2〜4 m 内側に切れ込んでいた。
- 縁石の平滑化（`kerb_widths`）：前後 `KERB_WIN` 点のうち `KERB_HITS` 点以上で赤が見つかれば縁石があるとする。
  縁石は短いので、路面の端（10 m の中央値）より狭い窓にする。はぐれた赤の画素 1 つ（看板・車など）は
  `KERB_HITS` で捨てる。`KERB_GAP` 点以下の切れ目は縁石として埋める。幅は、縁石が続く所ごとに 1 つの値
  （赤が見つかった点の幅の中央値）にする。点ごとの中央値では、外の縁が点ごとに 0.25〜0.75 m 揺れ、
  写真ではまっすぐな縁石がのこぎりの歯のように描かれた（okayama の T10 の出口）。
- 幅の取り出し（`band`）：基準線の左向きの法線に沿って ±`REACH` m を `STEP` m 刻みで画素を読む。
  基準線から `START_MAX` m 以内で最も近い路面の画素から始め、左右へ路面が続く所まで伸ばす。
  `GAP_MAX` m 以下の切れ目（画素の色のばらつき）は越えるが、白の画素（`paint_masks`）では止まる。
  白線の先にまたアスファルトがある所（ピットレーンとの間の白線、走路の外の舗装した道）で、細い白線を越えて
  路面を広げないためである。白で止めないと、okayama の T1 の進入（s = 68〜124、ピットの出口の合流）と
  T3 の出口（s = 1382〜1449）で、右の端が白線の外の舗装まで伸び（最大 4.75 m）、
  白線を越えるかどうかが点ごとに変わって端がジグザグになった。
- 端は基準線からの符号付きの距離 [m] で持つ。`left_m` は左の端の位置（左が正）、`right_m` は右の端の位置を
  右を正として測った値。`left_kerb_m`・`right_kerb_m` は縁石の外の線で、同じ向きに測る。縁石のない所では路面の端と同じ値。基準線は路面の中心ではなく、路面の外を通る所もある（2026-11 の okayama では
  `s` の 17%、最大 3.5 m）。そのため端の値が負になることがある（例：`left_m = -1` は左の端が基準線の 1 m 右）。
- CSV の頭の `# attribution = ...`・`# attribution_url = ...` は出典の表記で、`analysis.export` がそのまま
  web view に渡す（地理院タイルを加工して使うときは出典を示す。国土地理院コンテンツ利用規約）。
- 外れ値の除去（`clean`）：幅（`left_m + right_m`）が `WIDTH_MIN`〜`WIDTH_MAX` m の外、または片側が
  `SIDE_MIN` m 未満の `s` を捨てる。残りに前後 `SMOOTH` m の中央値をかけ、捨てた所もこの中央値で埋める。
  中央値の窓に有効な値が 1 つもない所（捨てた所が `2 * SMOOTH` m を超えて続く所）があれば失敗させる。
"""

import math
import sys
import tomllib
import urllib.request
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from ingest.racechrono import to_enu
from ingest.track import TRACK_DIR, Refline, frame, load_refline

ZOOM = 18
TILE = 256
LAYER = "seamlessphoto"
TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
CACHE_DIR = Path.home() / ".cache" / "lapdiff" / "gsi" / LAYER
USER_AGENT = "lapdiff-edges/0.1"
ATTRIBUTION = "国土地理院 地理院タイル（全国最新写真（シームレス））を加工して作成"
ATTRIBUTION_URL = "https://maps.gsi.go.jp/development/ichiran.html"

MARGIN = 50.0
LUM_MAX = 160.0
BLUE_MIN = 8.0
REACH = 15.0
STEP = 0.25
START_MAX = 4.0
GAP_MAX = 0.5
WHITE_LUM = 175.0
WHITE_SAT = 18.0
WHITE_RB = 12.0
RED_RG = 11.0
RED_RB = 10.0
RED_LUM = 212.0
LINE_SEARCH = 1.5
LINE_MAX = 0.75
KERB_MAX = 2.5
KERB_WIN = 3
KERB_HITS = 3
KERB_GAP = 8
WIDTH_MIN = 8.0
WIDTH_MAX = 20.0
SIDE_MIN = -4.0
SMOOTH = 10


def road_mask(rgb: np.ndarray) -> np.ndarray:
    """画素（最後の軸が R, G, B）が路面か。"""
    rgb = np.asarray(rgb, dtype=np.float64)
    return (rgb.mean(axis=-1) < LUM_MAX) & (rgb[..., 2] - rgb[..., 0] >= BLUE_MIN)


def band(road: np.ndarray, white: np.ndarray, offs: np.ndarray) -> tuple[float, float]:
    """法線上の路面の判定 `road` と白の判定 `white`（横の位置 `offs` [m] ごと、左が正）から (left_m, right_m)。

    `START_MAX` m 以内に路面がなければ (nan, nan)。白の画素では切れ目の長さによらず止まる。
    """
    near = np.flatnonzero(road & (np.abs(offs) <= START_MAX))
    if near.size == 0:
        return math.nan, math.nan
    i0 = int(near[np.argmin(np.abs(offs[near]))])
    max_gap = round(GAP_MAX / STEP)

    def walk(step: int) -> float:
        i, last, gap = i0, i0, 0
        while 0 <= i + step < len(offs):
            i += step
            if road[i]:
                last, gap = i, 0
            elif white[i]:
                break
            else:
                gap += 1
                if gap > max_gap:
                    break
        return float(offs[last])

    return walk(1), -walk(-1)


def paint_masks(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """画素（最後の軸が R, G, B）が (白, 縁石の赤, 色のない灰色, 暗い灰色) か。初めの 3 つは重ならない。

    灰色は白より暗い無彩色（くすんだアスファルト・白線のぼけた縁・舗装した退避帯）。白線・縁石を探すとき、
    切れ目に数えずに越える。暗い灰色は灰色のうち明るさが `LUM_MAX` 未満の画素（青みを失ったアスファルト）。
    """
    rgb = np.asarray(rgb, dtype=np.float64)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = rgb.mean(axis=-1)
    red = (r - g >= RED_RG) & (r - b >= RED_RB) & (lum < RED_LUM)
    neutral = (rgb.max(axis=-1) - rgb.min(axis=-1) <= WHITE_SAT) & (r - b <= WHITE_RB) & ~red
    white = neutral & (lum >= WHITE_LUM)
    grey = neutral & ~white
    return white, red, grey, grey & (lum < LUM_MAX)


def rim(white: np.ndarray, red: np.ndarray, grey: np.ndarray, dark: np.ndarray, offs: np.ndarray, edge: float,
        sign: int) -> tuple[float, float]:
    """アスファルトの端 `edge`（`offs` 上の位置 [m]）から `sign` の向き（+1 が左）に白線と縁石を読む。

    (白線を含めた路面の端, 縁石の外の線) を `offs` 上の位置で返す。白線がなければ前者は `edge`、
    赤がなければ後者は nan。白・赤・灰色が続く所だけを読み、それ以外が `GAP_MAX` m を超えて続けば止める。
    白線は、アスファルトの端から `LINE_SEARCH` m 以内で始まる最初の白の並びで、幅は `LINE_MAX` m まで。
    アスファルトの端から暗い灰色 `dark` が途切れずに続く所は、`LINE_SEARCH` をその外側から測る。
    縁石の外の線は、白線の外から `KERB_MAX` m 以内の最も外の赤。
    """
    if not math.isfinite(edge):
        return math.nan, math.nan
    i = int(np.argmin(np.abs(offs - edge)))
    max_gap = round(GAP_MAX / STEP)
    eps = 1e-9
    line_start = line_end = math.nan
    reds: list[float] = []
    gap = 0
    base, dusty = 0.0, True
    while 0 <= i + sign < len(offs):
        i += sign
        d = (offs[i] - edge) * sign
        dusty = dusty and bool(dark[i])
        if dusty:
            base = d
        if d > base + LINE_SEARCH + LINE_MAX + KERB_MAX + eps:
            break
        if white[i] and not reds:
            if math.isnan(line_start) and d <= base + LINE_SEARCH + eps:
                line_start = line_end = d
            elif abs(line_end - (d - STEP)) < eps and d - line_start <= LINE_MAX + eps:
                line_end = d
        if red[i]:
            reds.append(d)
        if white[i] or red[i] or grey[i]:
            gap = 0
        else:
            gap += 1
            if gap > max_gap:
                break
    paved = 0.0 if math.isnan(line_end) else line_end
    kerb = [d for d in reds if paved < d <= paved + KERB_MAX + eps]
    return edge + sign * paved, (edge + sign * max(kerb)) if kerb else math.nan


def runs(mask: np.ndarray) -> list[np.ndarray]:
    """閉じた線の上で `mask` が True の所が続く区間ごとの添字（s = 0 をまたぐ区間は 1 つにまとめる）。"""
    n = len(mask)
    if mask.all():
        return [np.arange(n)]
    order = (np.arange(n) + int(np.flatnonzero(~mask)[0])) % n  # False から始めて、区間を途中で切らない
    m = mask[order]
    edges_ = np.flatnonzero(np.diff(np.r_[0, m.astype(int), 0]))
    return [order[a:b] for a, b in zip(edges_[::2], edges_[1::2])]


def kerb_widths(paved: np.ndarray, kerb: np.ndarray, win: int = KERB_WIN, hits: int = KERB_HITS,
                max_gap: int = KERB_GAP) -> np.ndarray:
    """縁石の幅 [m]（路面の端から外へ、0 以上）。`paved` は平滑化した路面の端、`kerb` は各点の縁石の外の線
    （同じ向きに測った値、赤がなければ nan）。閉じた線として前後 `win` 点で見る。
    `max_gap` 点以下の切れ目は埋め、縁石が続く区間ごとに幅を 1 つの値（区間の中の幅の中央値）にする。"""
    w = np.where(np.isfinite(kerb), np.clip(kerb - paved, 0.0, None), np.nan)
    n = len(w)
    idx = (np.arange(n)[:, None] + np.arange(-win, win + 1)) % n
    found = np.isfinite(w[idx]).sum(axis=1) >= hits
    out = np.zeros(n)
    if not found.any():
        return out
    for gap in runs(~found):
        if len(gap) <= max_gap:
            found[gap] = True
    for run in runs(found):
        out[run] = np.nanmedian(w[run])
    return out


def clean(left: np.ndarray, right: np.ndarray, smooth: int = SMOOTH) -> tuple[np.ndarray, np.ndarray]:
    """外れ値を捨て、閉じた線として前後 `smooth` 点の中央値をかける。埋められない所があれば失敗させる。"""
    width = left + right
    ok = np.isfinite(width) & (width >= WIDTH_MIN) & (width <= WIDTH_MAX)
    ok &= (left >= SIDE_MIN) & (right >= SIDE_MIN)
    n = len(left)
    idx = (np.arange(n)[:, None] + np.arange(-smooth, smooth + 1)) % n

    def med(v: np.ndarray) -> np.ndarray:
        w = np.where(ok, v, np.nan)[idx]
        empty = ~np.isfinite(w).any(axis=1)
        if empty.any():
            bad = np.flatnonzero(empty)
            raise ValueError(
                f"路面の幅が取れない所が {2 * smooth} 点を超えて続く（{bad.size} 点、"
                f"添字 {bad[0]}〜{bad[-1]}）。写真と基準線の重なりを確かめる"
            )
        return np.nanmedian(w, axis=1)

    return med(left), med(right)


# --- 座標 ---

def from_enu(x, y, lat0: float, lon0: float) -> tuple[np.ndarray, np.ndarray]:
    """`to_enu` の逆。接平面の近似から始め、`to_enu` との差で 3 回直す。"""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    m_per_lat = 111_000.0
    m_per_lon = m_per_lat * math.cos(math.radians(lat0))
    lat, lon = lat0 + y / m_per_lat, lon0 + x / m_per_lon
    for _ in range(3):
        ex, ey = to_enu(lat, lon, lat0, lon0)
        lat, lon = lat + (y - ey) / m_per_lat, lon + (x - ex) / m_per_lon
    return lat, lon


def tile_xy(lat, lon, zoom: int = ZOOM) -> tuple[np.ndarray, np.ndarray]:
    """緯度・経度 [deg] の、Web メルカトルのタイル座標（整数部がタイル番号、小数部がタイル内の位置）。"""
    n = 2**zoom
    lat_r = np.radians(np.asarray(lat, dtype=np.float64))
    tx = (np.asarray(lon, dtype=np.float64) + 180) / 360 * n
    ty = (1 - np.arcsinh(np.tan(lat_r)) / math.pi) / 2 * n
    return tx, ty


# --- タイル ---

def tile_path(x: int, y: int, zoom: int = ZOOM, cache: Path = CACHE_DIR) -> Path:
    return cache / f"{zoom}_{x}_{y}.jpg"


def fetch_tile(x: int, y: int, zoom: int = ZOOM, cache: Path = CACHE_DIR) -> Path:
    path = tile_path(x, y, zoom, cache)
    if path.exists():
        return path
    url = TILE_URL.format(z=zoom, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as res:
        body = res.read()
    Image.open(BytesIO(body)).verify()  # JPEG でなければ（エラーの HTML など）ここで失敗する
    cache.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


class Mosaic:
    """タイルを貼り合わせた画像と、ENU から画素への変換。"""

    def __init__(self, line: Refline, lat0: float, lon0: float, cache: Path = CACHE_DIR):
        self.lat0, self.lon0 = lat0, lon0
        xs = [line.x.min() - MARGIN, line.x.max() + MARGIN]
        ys = [line.y.min() - MARGIN, line.y.max() + MARGIN]
        lat, lon = from_enu(np.array([xs[0], xs[1], xs[0], xs[1]]), np.array([ys[0], ys[0], ys[1], ys[1]]),
                            lat0, lon0)
        tx, ty = tile_xy(lat, lon)
        self.x0, self.y0 = int(tx.min()), int(ty.min())
        nx, ny = int(tx.max()) - self.x0 + 1, int(ty.max()) - self.y0 + 1
        img = Image.new("RGB", (TILE * nx, TILE * ny))
        paths = []
        for i in range(nx):
            for j in range(ny):
                p = fetch_tile(self.x0 + i, self.y0 + j, cache=cache)
                paths.append(p)
                img.paste(Image.open(p).convert("RGB"), (TILE * i, TILE * j))
        self.image = img
        self.rgb = np.asarray(img)
        self.tiles = (nx, ny)
        days = sorted(datetime.fromtimestamp(p.stat().st_mtime, UTC).date() for p in paths)
        self.fetched = (days[0], days[-1])

    def pixel(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        tx, ty = tile_xy(*from_enu(x, y, self.lat0, self.lon0))
        return (tx - self.x0) * TILE, (ty - self.y0) * TILE

    def sample(self, x, y) -> np.ndarray:
        px, py = self.pixel(x, y)
        h, w = self.rgb.shape[:2]
        # 画素 j は座標 [j, j + 1) を覆う（中心は j + 0.5）。丸めると半画素（約 0.25 m）東・南の画素を読む
        i, j = np.floor(py).astype(int), np.floor(px).astype(int)
        if (i < 0).any() or (i >= h).any() or (j < 0).any() or (j >= w).any():
            raise ValueError("画像の外を読もうとした（MARGIN が REACH より小さい？）")
        return self.rgb[i, j]


def raw_edges(line: Refline, mosaic: Mosaic) -> dict[str, np.ndarray]:
    """基準線の格子の各 `s` での、外れ値を捨てる前の値。

    `left`・`right` は白線を含めた路面の端、`left_kerb`・`right_kerb` は縁石の外の線（赤がなければ nan）。
    どれも `left_m`・`right_m` と同じ向き（左は左が正、右は右が正）。
    """
    offs = np.arange(-REACH, REACH + STEP / 2, STEP)
    rx, ry, nx, ny = frame(line, line.s)
    rgb = mosaic.sample(rx[:, None] + offs * nx[:, None], ry[:, None] + offs * ny[:, None])
    road = road_mask(rgb)
    white, red, grey, dark = paint_masks(rgb)
    out = []
    for r, w, k, g, dk in zip(road, white, red, grey, dark):
        left, right = band(r, w, offs)
        ll, lk = rim(w, k, g, dk, offs, left, 1)
        rl, rk = rim(w, k, g, dk, offs, -right, -1)
        out.append((ll, -rl, lk, -rk))
    a = np.array(out)
    return {"left": a[:, 0], "right": a[:, 1], "left_kerb": a[:, 2], "right_kerb": a[:, 3]}


def write_edges(path: Path, s: np.ndarray, left: np.ndarray, right: np.ndarray,
                left_kerb: np.ndarray, right_kerb: np.ndarray, note: str) -> None:
    head = "".join(f"# {t}\n" for t in note.splitlines())
    body = "s,left_m,right_m,left_kerb_m,right_kerb_m\n" + "".join(
        f"{a:.3f},{b:.2f},{c:.2f},{d:.2f},{e:.2f}\n" for a, b, c, d, e in zip(s, left, right, left_kerb, right_kerb))
    path.write_text(head + body, encoding="utf-8")


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        raise SystemExit("usage: python -m ingest.edges <course>")
    course_dir = TRACK_DIR / argv[0]
    line = load_refline(course_dir)
    origin = tomllib.loads((course_dir / "origin.toml").read_text(encoding="utf-8"))
    mosaic = Mosaic(line, origin["lat"], origin["lon"])
    raw = raw_edges(line, mosaic)
    left, right = clean(raw["left"], raw["right"])
    left_kerb = left + kerb_widths(left, raw["left_kerb"])
    right_kerb = right + kerb_widths(right, raw["right_kerb"])
    width = left + right
    raw_ok = np.isfinite(raw["left"] + raw["right"]).mean()
    note = (
        "コースの端（ingest.edges が書く）。s は基準線（refline.csv）の格子、"
        "left_m は左の端（左が正）、right_m は右の端（右が正）の基準線からの距離 [m]\n"
        f"attribution = {ATTRIBUTION}\n"
        f"attribution_url = {ATTRIBUTION_URL}\n"
        f"tiles = {TILE_URL.format(z=ZOOM, x='{x}', y='{y}')}、zoom = {ZOOM}、"
        f"{mosaic.tiles[0]}x{mosaic.tiles[1]} 枚、取得日 {mosaic.fetched[0]}〜{mosaic.fetched[1]}\n"
        f"路面の画素：明るさ < {LUM_MAX:g}、B - R >= {BLUE_MIN:g}。法線上 ±{REACH:g} m を {STEP:g} m 刻み、"
        f"始点は基準線から {START_MAX:g} m 以内、{GAP_MAX:g} m 以下の切れ目は越える（白の画素では止まる）\n"
        f"白線と縁石：アスファルトの端から外へ白・赤・灰色の画素が続く所を読む。"
        f"白は明るさ >= {WHITE_LUM:g}、彩度 <= {WHITE_SAT:g}、R - B <= {WHITE_RB:g}（灰色は明るさだけ満たさない画素）。"
        f"赤は R - G >= {RED_RG:g}、R - B >= {RED_RB:g}、明るさ < {RED_LUM:g}。"
        f"白線（アスファルトの端から {LINE_SEARCH:g} m 以内で始まる最初の白の並び、幅 {LINE_MAX:g} m まで）は"
        f"路面（left_m・right_m、走路の限界は白線の外側）に含める。アスファルトの端から暗い灰色（明るさ < {LUM_MAX:g}）が"
        f"続く所は、その外側から {LINE_SEARCH:g} m を測る\n"
        f"left_kerb_m・right_kerb_m は縁石の外の線（白線の外から {KERB_MAX:g} m 以内の最も外の赤）。縁石のない所は路面の端と同じ。"
        f"前後 {KERB_WIN} 点（計 {2 * KERB_WIN + 1} 点）のうち {KERB_HITS} 点以上で赤があれば縁石とし、{KERB_GAP} 点以下の切れ目は埋める。"
        f"幅は縁石が続く区間ごとに 1 つの値（赤のあった点の幅の中央値）\n"
        f"外れ値：幅 {WIDTH_MIN:g}〜{WIDTH_MAX:g} m の外、片側 {SIDE_MIN:g} m 未満を捨てる。"
        f"前後 {SMOOTH} 点（{SMOOTH * line.length / len(line.s):.0f} m）の中央値で平滑化"
    )
    out = course_dir / "edges.csv"
    write_edges(out, line.s, left, right, left_kerb, right_kerb, note)
    kerb_m = ((left_kerb > left) | (right_kerb > right)).sum() * line.length / len(line.s)
    print(f"{out}: {len(line.s)} 点、幅の中央値 {np.median(width):.1f} m（{width.min():.1f}〜{width.max():.1f}）、"
          f"写真から幅が取れた点 {raw_ok:.1%}、縁石のある所 {kerb_m:.0f} m")


if __name__ == "__main__":
    main(sys.argv[1:])
