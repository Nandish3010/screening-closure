import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppProvider } from "../src/app/AppContext";
import type { ApiClient, Role } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";

export function renderWithApp(ui: ReactElement, opts: { api?: ApiClient; role?: Role; route?: string; path?: string } = {}) {
  const api = opts.api ?? createMockAdapter(null);
  const view = render(
    <MemoryRouter initialEntries={[opts.route ?? "/"]}>
      <AppProvider api={api} initialRole={opts.role ?? "coordinator"}>
        {opts.path ? <Routes><Route path={opts.path} element={ui} /></Routes> : ui}
      </AppProvider>
    </MemoryRouter>,
  );
  return { api, ...view };
}
