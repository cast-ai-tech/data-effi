import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BusinessModelPicker, CountryFlagPicker } from "@/components/MemberPickers";

const COUNTRIES = [
  { code: "GT", name: "Guatemala" },
  { code: "HN", name: "Honduras" },
  { code: "CR", name: "Costa Rica" },
];

afterEach(cleanup);

describe("CountryFlagPicker", () => {
  it("shows one flag button per country the company operates in", () => {
    render(<CountryFlagPicker countries={COUNTRIES} selected={[]} onChange={() => {}} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons.map((b) => b.textContent?.trim())).toEqual(["🇬🇹GT", "🇭🇳HN", "🇨🇷CR"]);
    for (const button of buttons) expect(button).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/ve todos los países/i)).toBeInTheDocument();
  });

  it("adds a country on click and removes it on a second click", () => {
    const onChange = vi.fn();
    render(<CountryFlagPicker countries={COUNTRIES} selected={["GT"]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /Honduras/ }));
    expect(onChange).toHaveBeenLastCalledWith(["GT", "HN"]);

    fireEvent.click(screen.getByRole("button", { name: /Guatemala/ }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("lights the selected flags and says which ones", () => {
    render(<CountryFlagPicker countries={COUNTRIES} selected={["GT", "HN"]} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /Guatemala/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Costa Rica/ })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/Solo GT, HN/)).toBeInTheDocument();
  });
});

describe("BusinessModelPicker", () => {
  it("offers the two sides of the business", () => {
    render(<BusinessModelPicker value={null} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "Ecommerce" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Proveeduría" })).toHaveAttribute("aria-pressed", "false");
  });

  it("selects on click and clears when the lit one is clicked again", () => {
    const onChange = vi.fn();
    const { rerender } = render(<BusinessModelPicker value={null} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Proveeduría" }));
    expect(onChange).toHaveBeenLastCalledWith("proveeduria");

    rerender(<BusinessModelPicker value="proveeduria" onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Proveeduría" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Proveeduría" }));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});
