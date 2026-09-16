import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
  Outlet,
  useRouterState,
} from "@tanstack/react-router";
import {
  Library,
  Activity,
  MessageSquare,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
  Moon,
  Sun,
  BookOpen,
  ChevronRight,
  ArrowUpRight,
} from "lucide-react";
import { DocumentsPage } from "./features/documents";

import { Button, ErrorBox } from "./components/ui";
import { useNav } from "./lib/navigation";
import { api, type Config } from "./lib/api";
import "./styles.css";

const WorkbenchLazy = React.lazy(() =>
  import("./features/workbench").then((m) => ({ default: m.WorkbenchPage })),
);
function WorkbenchPage() {
  return (
    <React.Suspense fallback={<div className="empty">正在读取页面…</div>}>
      <WorkbenchLazy />
    </React.Suspense>
  );
}
const RetrievalLazy = React.lazy(() =>
  import("./features/retrieval").then((m) => ({ default: m.RetrievalPage })),
);
function RetrievalPage() {
  return (
    <React.Suspense fallback={<div className="empty">正在读取页面…</div>}>
      <RetrievalLazy />
    </React.Suspense>
  );
}
const RunsLazy = React.lazy(() =>
  import("./features/runs").then((m) => ({ default: m.RunsPage })),
);
function RunsPage() {
  return (
    <React.Suspense fallback={<div className="empty">正在读取页面…</div>}>
      <RunsLazy />
    </React.Suspense>
  );
}
const SettingsLazy = React.lazy(() =>
  import("./features/settings").then((m) => ({ default: m.SettingsPage })),
);
function SettingsPage() {
  return (
    <React.Suspense fallback={<div className="empty">正在读取页面…</div>}>
      <SettingsLazy />
    </React.Suspense>
  );
}
function Shell() {
  const go = useNav(),
    path = useRouterState({ select: (s) => s.location.pathname }),
    [collapsed, setCollapsed] = useState(false),
    [dark, setDark] = useState(
      () => localStorage.getItem("paper-theme") === "dark",
    );
  React.useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("paper-theme", dark ? "dark" : "light");
  }, [dark]);
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => api<Config>("/config/public"),
  });
  const links = [
    { path: "/", label: "文献库", icon: Library },
    { path: "/runs", label: "运行记录", icon: Activity },
    { path: "/retrieval", label: "检索实验室", icon: MessageSquare },
    { path: "/settings", label: "系统状态", icon: Settings },
  ];
  const current = links.find((l) =>
    l.path === "/"
      ? path === "/" || path.startsWith("/documents")
      : path.startsWith(l.path),
  );
  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => go("/")}
          aria-label="论文 RAG 工作台首页"
        >
          <span className="brand-mark">
            <BookOpen size={22} />
          </span>
          <span>
            <strong>论文 RAG</strong>
            <small>RESEARCH WORKBENCH</small>
          </span>
        </button>
        <div className="workspace-label">
          <span className="workspace-avatar">研</span>
          <span>
            <strong>个人研究空间</strong>
            <small>本机工作台</small>
          </span>
          <ChevronRight size={14} />
        </div>
        <div className="nav-caption">工作空间</div>
        <nav aria-label="主导航">
          {links.map((l) => (
            <button
              key={l.path}
              className={current?.path === l.path ? "active" : ""}
              onClick={() => go(l.path)}
              title={l.label}
            >
              <l.icon size={18} />
              <span>{l.label}</span>
              {current?.path === l.path && <span className="nav-active-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="live-dot" />
            <strong>知识，从原文开始</strong>
            <p>
              保留每一个来源
              <br />
              连接每一条证据
            </p>
          </div>
          <button className="local-profile" onClick={() => go("/settings")}>
            <span className="avatar">L</span>
            <span>
              <strong>Local workspace</strong>
              <small>个人 · 本机运行</small>
            </span>
            <ArrowUpRight size={14} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="inline">
            <Button
              variant="ghost"
              size="icon"
              aria-label="折叠导航"
              onClick={() => setCollapsed(!collapsed)}
            >
              {collapsed ? (
                <PanelLeftOpen size={18} />
              ) : (
                <PanelLeftClose size={18} />
              )}
            </Button>
            <span className="muted">工作空间</span>
            <ChevronRight size={13} />
            <span>
              {path.startsWith("/documents") ? "论文工作台" : current?.label}
            </span>
          </div>
          <div className="inline">
            <span className="collection-pill">
              <span className="live-dot" />
              {config.data?.collection ?? "知识库"}
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={dark ? "切换浅色" : "切换深色"}
              onClick={() => setDark(!dark)}
            >
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </Button>
            <span className="avatar small">L</span>
          </div>
        </header>
        <main>
          <Outlet />
        </main>
        <footer>
          论文 RAG 工作台<span>原文 · 分块 · 证据</span>
        </footer>
      </div>
    </div>
  );
}
const root = createRootRoute({
  component: Shell,
  errorComponent: ({ error }) => <ErrorBox error={error} />,
  notFoundComponent: () => (
    <div className="empty">
      页面不存在 · <a href="/">返回文献库</a>
    </div>
  ),
});
const routes = [
  createRoute({
    getParentRoute: () => root,
    path: "/",
    component: DocumentsPage,
  }),
  createRoute({
    getParentRoute: () => root,
    path: "/documents/$documentId",
    component: WorkbenchPage,
    validateSearch: (s: Record<string, unknown>) => ({
      run: typeof s.run === "string" ? s.run : undefined,
      chunk: typeof s.chunk === "string" ? s.chunk : undefined,
      view: ["pdf", "raw", "normalized"].includes(String(s.view))
        ? String(s.view)
        : "pdf",
    }),
  }),
  createRoute({
    getParentRoute: () => root,
    path: "/runs",
    component: RunsPage,
    validateSearch: (s: Record<string, unknown>) => ({
      run: typeof s.run === "string" ? s.run : undefined,
    }),
  }),
  createRoute({
    getParentRoute: () => root,
    path: "/retrieval",
    component: RetrievalPage,
    validateSearch: (s: Record<string, unknown>) => ({
      run: typeof s.run === "string" ? s.run : undefined,
      doc: typeof s.doc === "string" ? s.doc : undefined,
    }),
  }),
  createRoute({
    getParentRoute: () => root,
    path: "/settings",
    component: SettingsPage,
  }),
];
const router = createRouter({ routeTree: root.addChildren(routes) });
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 15000, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
