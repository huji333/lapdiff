import { describe, expect, it } from "vitest";
import { equalScaleWidth } from "./charts";

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
