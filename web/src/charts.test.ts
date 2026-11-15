import { describe, expect, it } from "vitest";
import { equalScaleWidth, kerbInRange, stackLabels } from "./charts";

describe("equalScaleWidth", () => {
  it("x と y の 1 m あたりの px が等しい", () => {
    const [height, margin, xSpan, ySpan] = [900, 12, 522, 1157];
    const w = equalScaleWidth(height, margin, xSpan, ySpan);
    expect((w - 2 * margin) / xSpan).toBeCloseTo((height - 2 * margin) / ySpan, 9);
  });
  it("範囲が空なら失敗させる", () => {
    expect(() => equalScaleWidth(900, 12, 10, 0)).toThrow();
  });
});

describe("stackLabels", () => {
  const [w, h, gap] = [26, 13, 11];
  const overlaps = (pts: { x: number }[], ys: number[]) =>
    ys.some((a, i) => ys.some((b, j) => i < j && Math.abs(pts[i]!.x - pts[j]!.x) < w && Math.abs(a - b) < h));

  it("離れた点はそれぞれ真上に置く", () => {
    const pts = [
      { x: 0, y: 100 },
      { x: 100, y: 100 },
    ];
    expect(stackLabels(pts, w, h, gap)).toEqual([89, 89]);
  });

  it("近い点のラベルは重ならないよう上に積み、点より下には置かない", () => {
    const pts = [
      { x: 0, y: 100 },
      { x: 5, y: 102 },
      { x: 10, y: 98 },
      { x: 3, y: 101 },
    ];
    const ys = stackLabels(pts, w, h, gap);
    expect(overlaps(pts, ys)).toBe(false);
    ys.forEach((y, i) => expect(y).toBeLessThanOrEqual(pts[i]!.y - gap));
  });
});

describe("kerbInRange", () => {
  // 添字 10〜13 の縁石：路面の端を 10→13 と進み、外の線を 13→10 と戻る
  const kerb = {
    side: "left" as const,
    i: [10, 11, 12, 13, 13, 12, 11, 10],
    x: [0, 1, 2, 3, 3, 2, 1, 0],
    y: [0, 0, 0, 0, 1, 1, 1, 1],
  };
  it("範囲の点を進む側・戻る側とも残し、閉じた帯にする", () => {
    expect(kerbInRange(kerb, 11, 12)).toEqual([
      [1, 0],
      [2, 0],
      [2, 1],
      [1, 1],
    ]);
  });
  it("範囲に掛からなければ null", () => {
    expect(kerbInRange(kerb, 20, 30)).toBeNull();
    expect(kerbInRange(kerb, 13, 30)).toBeNull();
  });
});
