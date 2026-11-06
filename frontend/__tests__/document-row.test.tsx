import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import DocumentRow from "@/components/DocumentRow";
import type { DocumentRecord } from "@/lib/api";

/**
 * __tests__/document-row.test.tsx — A row asks before it deletes, and only about itself.
 *
 * The confirmation used to be one `confirming: string | null` held by the panel, so opening a second row's
 * question closed the first — behaviour nobody chose, only what a single id made easy. The last case here
 * is the one that would have failed before the state moved down into the row.
 */

function record(overrides: Partial<DocumentRecord> = {}): DocumentRecord {
  return {
    document_id: "doc-1",
    owner_id: "owner-1",
    filename: "loan.pdf",
    content_type: "application/pdf",
    status: "indexed",
    page_count: 12,
    chunk_count: 34,
    size_bytes: 2 * 1024 * 1024,
    uploaded_at: "2026-11-06T00:00:00Z",
    error: null,
    ...overrides,
  };
}

function renderRow(document = record(), extra: Partial<Parameters<typeof DocumentRow>[0]> = {}) {
  return render(
    <ul>
      <DocumentRow
        document={document}
        selected={false}
        onToggleScope={vi.fn()}
        onRemove={vi.fn()}
        {...extra}
      />
    </ul>,
  );
}

describe("DocumentRow", () => {
  it("reports what the file is, in words", () => {
    renderRow();

    expect(screen.getByText("loan.pdf")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText(/34 chunks/)).toBeInTheDocument();
    expect(screen.getByText("2.0 MB")).toBeInTheDocument();
  });

  it("cannot be searched while it is still indexing", () => {
    renderRow(record({ status: "parsing", chunk_count: 0, page_count: 0 }));

    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByText("indexing")).toBeInTheDocument();
    // Chunk and page counts describe a finished index, so an unfinished one says nothing about them.
    expect(screen.queryByText(/chunks/)).not.toBeInTheDocument();
  });

  it("shows the backend's own reason when the file failed", () => {
    renderRow(record({ status: "failed", error: "No text layer: this PDF is a scan." }));

    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("No text layer: this PDF is a scan.")).toBeInTheDocument();
  });

  it("asks before deleting, and only deletes on the answer", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    renderRow(record(), { onRemove });

    await user.click(screen.getByRole("button", { name: "Delete loan.pdf" }));
    expect(onRemove).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(onRemove).toHaveBeenCalledWith("doc-1");
    // The row is on its way out, so its question goes back to being an icon.
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });

  it("puts the question away when told no", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    renderRow(record(), { onRemove });

    await user.click(screen.getByRole("button", { name: "Delete loan.pdf" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onRemove).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Delete loan.pdf" })).toBeInTheDocument();
  });

  it("lets two rows ask at once", async () => {
    const user = userEvent.setup();
    render(
      <ul>
        <DocumentRow
          document={record({ document_id: "doc-a", filename: "a.pdf" })}
          selected={false}
          onToggleScope={vi.fn()}
          onRemove={vi.fn()}
        />
        <DocumentRow
          document={record({ document_id: "doc-b", filename: "b.pdf" })}
          selected={false}
          onToggleScope={vi.fn()}
          onRemove={vi.fn()}
        />
      </ul>,
    );

    await user.click(screen.getByRole("button", { name: "Delete a.pdf" }));
    await user.click(screen.getByRole("button", { name: "Delete b.pdf" }));

    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(2);
  });
});
