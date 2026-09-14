import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { useQuery } from "./api";
import Compare from "./pages/Compare";
import Dashboard from "./pages/Dashboard";
import DealerDetailPage from "./pages/DealerDetail";
import Profile from "./pages/Profile";
import type { Health } from "./types";

export default function App() {
  const health = useQuery<Health>("/system/health");

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          dealbench<span>negotiation state engine</span>
        </div>
        <nav className="top">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/compare">Compare</NavLink>
          <NavLink to="/profile">Profile</NavLink>
        </nav>
        <span className="spacer" />
        {health.data && (
          <span
            className={`chip ${health.data.llm_enabled ? "warn" : "good"}`}
            title={
              health.data.llm_enabled
                ? `Text may be sent to the "${health.data.llm_provider}" provider.`
                : "No LLM is configured. Nothing leaves this machine."
            }
          >
            {health.data.llm_enabled
              ? `LLM: ${health.data.llm_provider}`
              : "Local only"}
          </span>
        )}
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/dealers/:id" element={<DealerDetailPage />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
