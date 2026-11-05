import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PrototypeStrip } from "../src/components/DemoBanner";

const css = readFileSync("src/styles.css", "utf8");
const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
const token = (name: string, dark = false) => {
  const block = dark ? css.split("prefers-color-scheme: dark")[1] : css;
  return new RegExp(`--${name}: (#[0-9a-f]{6})`).exec(block)![1];
};

describe("app-wide chrome", () => {
  it("the prototype strip says what this is and is not", () => {
    render(<PrototypeStrip />);
    expect(screen.getByTestId("proto-strip")).toHaveTextContent("Prototype for FHDIC Challenge 05. Not an official government service.");
  });
  it("the focus ring has 3:1 non-text contrast against its surfaces, light and dark", () => {
    expect(ratio(token("focus"), "#ffffff")).toBeGreaterThanOrEqual(3);
    expect(ratio(token("focus"), token("bg"))).toBeGreaterThanOrEqual(3);
    expect(ratio(token("focus"), token("sim-bg"))).toBeGreaterThanOrEqual(3); // on the amber strip
    expect(ratio(token("focus", true), token("bg", true))).toBeGreaterThanOrEqual(3);
    expect(ratio(token("focus", true), token("surface", true))).toBeGreaterThanOrEqual(3);
  });
});
