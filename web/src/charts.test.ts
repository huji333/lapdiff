import { describe, expect, it } from "vitest";
import { equalScaleWidth, stackLabels } from "./charts";

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
