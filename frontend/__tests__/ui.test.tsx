import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import EmptyNote from "@/components/ui/EmptyNote";
import { CloseIcon } from "@/components/ui/icons";
import Spinner from "@/components/ui/Spinner";
import StatusPill from "@/components/ui/StatusPill";

/**
 * __tests__/ui.test.tsx — The rules the primitives exist to hold.
 *
 * Not a screenshot test in disguise: these check the behaviour a hand-typed class list gets wrong
 * eventually — that a control's state is announced to assistive tech, that "waiting for the server" never
 * borrows the styling of "not available", and that a decorative glyph or an empty-list note is markup a
 * validator accepts.
 */

describe("Button", () => {
  it("is a plain button unless told otherwise", () => {
    render(<Button>Ask</Button>);

    expect(screen.getByRole("button", { name: "Ask" })).toHaveAttribute("type", "button");
  });

  it("keeps the colour it had when it was pressed, while a request is in flight", () => {
    render(
      <Button busy disabled>
        Open workspace
      </Button>,
    );

    const control = screen.getByRole("button", { name: "Open workspace" });
    // The regression this guards: a busy primary button fading to the disabled grey reads as "this
    // control broke", when the truth is "the server has not answered yet".
    expect(control.className).toContain("bg-indigo-600");
    expect(control.className).not.toContain("disabled:bg-zinc-800");
    expect(control).toHaveAttribute("aria-busy", "true");
  });

  it("greys out a primary button that has nothing to do", () => {
    render(
      <Button disabled>
        Open workspace
      </Button>,
    );

    const control = screen.getByRole("button", { name: "Open workspace" });
    expect(control.className).toContain("disabled:bg-zinc-800");
    expect(control).not.toHaveAttribute("aria-busy");
  });

  it("spins in place of a caller that asked for a spinner", () => {
    render(<Button busy>Upload documents</Button>);

    expect(document.querySelector("svg.animate-spin")).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveTextContent("Upload documents");
  });

  it("lets a submit button submit", () => {
    render(
      <Button type="submit" size="sm">
        Ask
      </Button>,
    );

    expect(screen.getByRole("button", { name: "Ask" })).toHaveAttribute("type", "submit");
  });
});

describe("Spinner", () => {
  it("is invisible to a screen reader, because the control already says it is busy", () => {
    render(<Spinner />);

    expect(document.querySelector("svg")).toHaveAttribute("aria-hidden");
  });
});

describe("Banner", () => {
  it("adopts the tone the caller chose and keeps its own words", () => {
    render(<Banner tone="warning">Confirm your address first.</Banner>);

    const banner = screen.getByText("Confirm your address first.");
    expect(banner.className).toContain("bg-amber-950/40");
    expect(banner.className).not.toContain("bg-red-950/50");
  });

  it("announces itself only when the caller asks it to", () => {
    const { unmount } = render(
      <Banner role="alert" id="auth-error">
        Wrong password.
      </Banner>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Wrong password.");
    unmount();

    render(<Banner>Confirmation link sent.</Banner>);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("is a div, so the amber one can hold a button", () => {
    render(
      <Banner tone="warning">
        <button type="button">Send the confirmation link again</button>
      </Banner>,
    );

    expect(screen.getByRole("button")).toBeInTheDocument();
    expect(document.querySelector("p")).toBeNull();
  });
});

describe("StatusPill", () => {
  it("calls `uploading` what the reader actually sees", () => {
    render(<StatusPill status="uploading" />);

    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("names every state in words, not only in colour", () => {
    const { rerender } = render(<StatusPill status="indexed" />);
    expect(screen.getByText("ready")).toBeInTheDocument();

    rerender(<StatusPill status="failed" />);
    expect(screen.getByText("failed")).toBeInTheDocument();
  });
});

describe("icons", () => {
  it("stays out of the way of a screen reader, because the button names itself", () => {
    render(
      <Button aria-label="Delete loan.pdf">
        <CloseIcon />
      </Button>,
    );

    expect(document.querySelector("svg")).toHaveAttribute("aria-hidden");
  });
});

describe("EmptyNote", () => {
  it("is a list item, so a list can hold it", () => {
    render(
      <ul>
        <EmptyNote>Nothing uploaded yet.</EmptyNote>
      </ul>,
    );

    const note = screen.getByText("Nothing uploaded yet.");
    expect(note.tagName).toBe("LI");
    expect(note.parentElement?.tagName).toBe("UL");
  });
});
