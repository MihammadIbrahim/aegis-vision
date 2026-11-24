import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { useAsync } from "../../hooks/useAsync";
import { useConfirm } from "../../hooks/useConfirm";
import { formatLocationPath } from "../../lib/format";
import { Button, DataTable, Dialog, Field, PageHeader, Pagination, StatusBadge, ToastProvider, useToast } from ".";

describe("Button", () => {
  it("applies the variant, hides the icon from screen readers and blocks clicks while loading", async () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <Button variant="danger" icon={Trash2} onClick={onClick}>
        Revoke
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Revoke" });
    expect(button.className).toContain("btn-danger");
    expect(button.getAttribute("type")).toBe("button"); // never submits a form by accident
    expect(button.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(
      <Button loading onClick={onClick}>
        Revoke
      </Button>,
    );
    expect((screen.getByRole("button", { name: "Revoke" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Revoke" }).getAttribute("aria-busy")).toBe("true");
  });
});

describe("Field", () => {
  it("links the label, hint and error to the control", () => {
    render(
      <Field label="Email Address" required hint="Unique in this campus" error="Not a valid email address.">
        <input />
      </Field>,
    );
    const input = screen.getByLabelText(/Email Address/) as HTMLInputElement;
    expect(input.required).toBe(true);
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const described = (input.getAttribute("aria-describedby") ?? "").split(" ");
    expect(described.map((id) => document.getElementById(id)?.textContent)).toEqual([
      "Unique in this campus",
      "Not a valid email address.",
    ]);
    expect(screen.getByTitle("Required").getAttribute("aria-hidden")).toBe("true");
  });

  it("marks optional fields in words", () => {
    render(
      <Field label="Phone">
        <input />
      </Field>,
    );
    expect(screen.getByText("(optional)")).toBeTruthy();
    expect(screen.getByLabelText(/Phone/).getAttribute("aria-invalid")).toBeNull();
  });
});

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <Dialog open={open} title="Revoke access" onClose={() => setOpen(false)}>
        <input aria-label="First" />
        <button type="button">Last</button>
      </Dialog>
    </>
  );
}

describe("Dialog", () => {
  it("moves focus inside, keeps Tab inside, closes on Escape and returns focus", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Revoke access" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));
    screen.getByRole("button", { name: "Last" }).focus();
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" })); // wrapped around
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Last" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});

function ConfirmHarness({ onDone }: { onDone: (r: unknown) => void }) {
  const { confirm, dialog } = useConfirm();
  return (
    <>
      <button
        type="button"
        onClick={async () =>
          onDone(
            await confirm({
              title: "Deactivate Ayesha Khan?",
              message: "Their restricted-area access will be revoked.",
              confirmLabel: "Deactivate",
              tone: "danger",
              reason: { label: "Reason", required: true },
            }),
          )
        }
      >
        Deactivate…
      </button>
      {dialog}
    </>
  );
}

describe("useConfirm / ConfirmDialog", () => {
  it("requires the reason when asked and returns it", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    render(<ConfirmHarness onDone={onDone} />);
    await user.click(screen.getByRole("button", { name: "Deactivate…" }));
    const confirmButton = screen.getByRole("button", { name: "Deactivate" }) as HTMLButtonElement;
    expect(confirmButton.className).toContain("btn-danger");
    expect(confirmButton.disabled).toBe(true);
    expect(document.activeElement).toBe(screen.getByLabelText(/Reason/)); // focus starts in the reason box
    await user.type(screen.getByLabelText(/Reason/), "  Graduated  ");
    await user.click(confirmButton);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ confirmed: true, reason: "Graduated" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("returns not confirmed on Cancel or Escape", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    render(<ConfirmHarness onDone={onDone} />);
    await user.click(screen.getByRole("button", { name: "Deactivate…" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(onDone).toHaveBeenLastCalledWith({ confirmed: false, reason: null }));
    await user.click(screen.getByRole("button", { name: "Deactivate…" }));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(2));
    expect(onDone).toHaveBeenLastCalledWith({ confirmed: false, reason: null });
  });
});

function ToastHarness() {
  const toast = useToast();
  return (
    <>
      <button type="button" onClick={() => toast("success", "Access granted.")}>
        ok
      </button>
      <button type="button" onClick={() => toast("error", "Could not save.")}>
        fail
      </button>
    </>
  );
}

describe("Toast", () => {
  it("announces success politely and closes it; errors are assertive and stay until dismissed", async () => {
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <ToastHarness />
        </ToastProvider>,
      );
      fireEvent.click(screen.getByRole("button", { name: "ok" }));
      fireEvent.click(screen.getByRole("button", { name: "fail" }));
      expect(screen.getByRole("status").textContent).toContain("Access granted.");
      expect(screen.getByRole("alert").textContent).toContain("Could not save.");
      act(() => vi.advanceTimersByTime(7000));
      expect(screen.getByRole("status").textContent).not.toContain("Access granted.");
      expect(screen.getByRole("alert").textContent).toContain("Could not save.");
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      expect(screen.getByRole("alert").textContent).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("DataTable", () => {
  const columns = [
    { key: "name", header: "Name", render: (r: { id: string; name: string }) => r.name },
    { key: "act", header: "Actions", render: (r: { id: string; name: string }) => <button type="button">Open {r.name}</button> },
  ];

  it("renders a captioned table with real action buttons", () => {
    render(<DataTable caption="Personnel" columns={columns} rows={[{ id: "1", name: "Ali" }]} rowKey={(r) => r.id} />);
    expect(screen.getByRole("table", { name: "Personnel" })).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "Actions"]);
    expect(screen.getByRole("button", { name: "Open Ali" })).toBeTruthy();
  });

  it("shows loading and empty states inside the table", () => {
    const { rerender } = render(<DataTable caption="Personnel" columns={columns} rows={[]} rowKey={(r) => r.id} loading />);
    expect(screen.getByRole("status").textContent).toContain("Loading");
    rerender(<DataTable caption="Personnel" columns={columns} rows={[]} rowKey={(r) => r.id} empty="No personnel found." />);
    expect(screen.getByText("No personnel found.")).toBeTruthy();
  });
});

describe("Pagination", () => {
  it("shows the range and moves by one page", async () => {
    const onChange = vi.fn();
    render(<Pagination total={60} limit={25} offset={25} onChange={onChange} />);
    expect(screen.getByRole("navigation", { name: "Pagination" }).textContent).toContain("26–50 of 60");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(onChange.mock.calls).toEqual([[50], [0]]);
  });

  it("disables what is not possible", () => {
    render(<Pagination total={0} limit={25} offset={0} onChange={() => undefined} />);
    expect((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("navigation").textContent).toContain("0–0 of 0");
  });
});

describe("StatusBadge and PageHeader", () => {
  it("shows status as text with a hidden icon", () => {
    render(<StatusBadge status="SUSPENDED" />);
    const badge = screen.getByText("Suspended");
    expect(badge.className).toContain("tone-critical");
    expect(badge.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("renders a heading, description, back link and actions", () => {
    render(
      <MemoryRouter>
        <PageHeader title="Personnel Directory" description="This campus" back={{ to: "/personnel", label: "Directory" }} actions={<Button>Add</Button>} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Personnel Directory" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Directory" }).getAttribute("href")).toBe("/personnel");
    expect(screen.getByRole("button", { name: "Add" })).toBeTruthy();
  });
});

describe("useAsync and formatting", () => {
  it("loads, reports errors and reloads", async () => {
    let fail = true;
    const load = vi.fn(async () => {
      if (fail) throw new Error("Network down");
      return "data";
    });
    const { result } = renderHook(() => useAsync(load, []));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect((result.current.error as Error).message).toBe("Network down");
    fail = false;
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe("data"));
    expect(result.current.error).toBeNull();
  });

  it("ignores an older result that arrives late", async () => {
    const resolvers: Array<(v: string) => void> = [];
    const { result, rerender } = renderHook(({ key }) => useAsync(() => new Promise<string>((r) => resolvers.push(r)), [key]), {
      initialProps: { key: "a" },
    });
    rerender({ key: "b" });
    await waitFor(() => expect(resolvers.length).toBeGreaterThanOrEqual(2));
    act(() => resolvers[resolvers.length - 1]("new"));
    await waitFor(() => expect(result.current.data).toBe("new"));
    act(() => resolvers[0]("old"));
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current.data).toBe("new");
  });

  it("formats location paths", () => {
    expect(formatLocationPath("IT Block", null, "Server Room")).toBe("IT Block › Server Room");
    expect(formatLocationPath(null, undefined)).toBe("—");
  });
});
