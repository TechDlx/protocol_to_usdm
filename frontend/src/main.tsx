import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import ReviewPage from "./pages/ReviewPage";
import RunInspectorPage from "./pages/RunInspectorPage";
import StudiesPage from "./pages/StudiesPage";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root element missing from index.html");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <header className="app-header">
        <Link to="/" className="app-title">
          Protocol → USDM
        </Link>
        <span className="app-subtitle">USDM v4 · local</span>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<StudiesPage />} />
          <Route path="/studies/:slug/runs/:runId" element={<RunInspectorPage />} />
          <Route path="/studies/:slug/runs/:runId/review" element={<ReviewPage />} />
        </Routes>
      </main>
    </BrowserRouter>
  </StrictMode>,
);
