import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { Markdown } from "./markdown";
import { Status } from "./ui";
describe("Evidence reading", () => {
  it("selects research citations without changing ordinary citation handling", () => {
    const research = vi.fn(),
      ordinary = vi.fn();
    render(
      <Markdown
        text="研究结论 [E2]，检索结果 [3]。"
        onResearchCitation={research}
        onCitation={ordinary}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "E2" }));
    expect(research).toHaveBeenCalledWith("E2");
    fireEvent.click(screen.getByRole("button", { name: "3" }));
    expect(ordinary).toHaveBeenCalledWith(3);
  });
  it("does not interpret figure panel labels as website links", () => {
    render(<Markdown text="See [FIG_REF: fig_2](a)." />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("See [FIG_REF: fig_2](a).")).toBeVisible();
  });
  it("keeps table cells while stripping event handlers and scripts", () => {
    const { container } = render(
      <Markdown
        text={
          '<table onclick="alert(1)"><tr><td>证据 A</td></tr></table><script>alert(1)</script>'
        }
      />,
    );
    expect(screen.getByRole("cell", { name: "证据 A" })).toBeVisible();
    expect(container.querySelector("[onclick],script")).toBeNull();
  });
  it("turns a citation into a working evidence selection", () => {
    const select = vi.fn();
    render(<Markdown text="结论由涨落产生 [3]。" onCitation={select} />);
    fireEvent.click(screen.getByRole("button", { name: "3" }));
    expect(select).toHaveBeenCalledWith(3);
  });
  it("does not render raw executable HTML or fetch remote images", () => {
    const { container } = render(
      <Markdown
        text={
          "<script>alert(1)</script>\n\n![remote](https://example.com/track.png)"
        }
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });
  it("renders formulas and uses explicit failure labels", () => {
    const { container } = render(
      <>
        <Markdown text={"$$x^2 + y^2$$"} />
        <Status value="failed" />
      </>,
    );
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(screen.getByText("失败")).toBeVisible();
  });
});
