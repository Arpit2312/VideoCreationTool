import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Clapperboard,
  FolderKanban,
  Gauge,
  HeartPulse,
  Image,
  Layers,
  ListVideo,
  Menu,
  Moon,
  PlusCircle,
  Settings as SettingsIcon,
  Sun,
  X,
} from "lucide-react";
import { api } from "./api";
import { useTheme } from "./lib/theme";
import { useServerEvents } from "./lib/events";
import { cx, SimulatedBadge, ZeroCostBadge } from "./components/ui";
import Dashboard from "./pages/Dashboard";
import NewVideo from "./pages/NewVideo";
import BatchStudio from "./pages/BatchStudio";
import ActiveJobs from "./pages/ActiveJobs";
import Projects from "./pages/Projects";
import VideoWorkspace from "./pages/VideoWorkspace";
import AssetsPage from "./pages/Assets";
import RenderQueue from "./pages/RenderQueue";
import SettingsPage from "./pages/Settings";
import RepoHealth from "./pages/RepoHealth";

const NAV = [
  { to: "/", label: "Dashboard", icon: Gauge, end: true },
  { to: "/new", label: "New Video", icon: PlusCircle },
  { to: "/batch", label: "Batch Studio", icon: Layers },
  { to: "/jobs", label: "Active Jobs", icon: Activity },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/assets", label: "Assets", icon: Image },
  { to: "/queue", label: "Render Queue", icon: ListVideo },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/health", label: "Repository Health", icon: HeartPulse },
];

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const conn = useServerEvents();
  const [navOpen, setNavOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setNavOpen(false), [loc.pathname]);

  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 60_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.systemHealth, staleTime: 60_000, retry: 0 });
  const appName = settings.data?.app_name || "SpaceStudio";
  useEffect(() => {
    document.title = appName;
  }, [appName]);

  const simulated = health.data?.claude?.simulated;

  return (
    <div className="flex h-full">
      {navOpen && <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setNavOpen(false)} />}
      <aside
        className={cx(
          "fixed z-40 inset-y-0 left-0 w-60 border-r border-line bg-panel flex flex-col transition-transform lg:static lg:translate-x-0",
          navOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-center gap-2 px-4 h-14 border-b border-line">
          <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 grid place-items-center text-white">
            <Clapperboard className="h-4 w-4" />
          </div>
          <div className="font-semibold tracking-tight truncate">{appName}</div>
          <button className="ml-auto btn-ghost btn-sm lg:hidden" onClick={() => setNavOpen(false)} aria-label="Close menu">
            <X className="h-4 w-4" />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition",
                  isActive ? "bg-accent/15 text-fg font-medium" : "text-muted hover:text-fg hover:bg-panel2",
                )
              }
            >
              <n.icon className="h-4 w-4" />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t border-line space-y-2 text-xs">
          <div className="flex items-center gap-2 text-muted">
            <span
              className={cx(
                "h-2 w-2 rounded-full",
                conn === "open" ? "bg-emerald-500" : conn === "connecting" ? "bg-slate-400 animate-softpulse" : "bg-red-500",
              )}
            />
            {conn === "open" ? "Live updates connected" : conn === "connecting" ? "Connecting…" : "Live updates offline — retrying"}
          </div>
          <button className="btn w-full" onClick={toggleTheme}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
        </div>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="h-14 shrink-0 border-b border-line bg-panel/80 backdrop-blur flex items-center gap-2 px-4">
          <button className="btn-ghost btn-sm lg:hidden" onClick={() => setNavOpen(true)} aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          <span className="font-semibold lg:hidden truncate">{appName}</span>
          <div className="ml-auto flex items-center gap-2">
            {simulated && <SimulatedBadge />}
            {settings.data && <ZeroCostBadge on={settings.data.zero_cost_default !== false} />}
            {health.data && !health.data.zero_cost_ready && (
              <NavLink to="/health" className="hidden sm:inline text-xs text-amber-600 dark:text-amber-300 hover:underline">
                Zero-cost not ready
              </NavLink>
            )}
          </div>
        </header>
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] p-4 sm:p-6">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/new" element={<NewVideo />} />
              <Route path="/batch" element={<BatchStudio />} />
              <Route path="/jobs" element={<ActiveJobs />} />
              <Route path="/projects" element={<Projects />} />
              <Route path="/projects/:id" element={<Projects />} />
              <Route path="/videos/:id" element={<VideoWorkspace />} />
              <Route path="/assets" element={<AssetsPage />} />
              <Route path="/queue" element={<RenderQueue />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/health" element={<RepoHealth />} />
              <Route path="*" element={<div className="text-muted">Page not found.</div>} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}
