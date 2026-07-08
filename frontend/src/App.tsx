import {
  BrowserRouter,
  Routes,
  Route,
  NavLink,
  useLocation,
} from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
import Predictions from "./pages/Predictions";
import DataSources from "./pages/DataSources";
import ShadowSafety from "./pages/ShadowSafety";
import OpsConsole from "./pages/OpsConsole";

const NAV = [
  { path: "/", label: "Dashboard" },
  { path: "/runs", label: "Runs" },
  { path: "/predictions", label: "Predictions" },
  { path: "/data-sources", label: "Data Sources" },
  { path: "/shadow-safety", label: "Shadow Safety" },
  { path: "/ops", label: "Ops Console" },
];

function Sidebar() {
  const loc = useLocation();
  return (
    <div className="sidebar">
      <h1>EFM3 Ledger</h1>
      {NAV.map((n) => (
        <NavLink
          key={n.path}
          to={n.path}
          end={n.path === "/"}
          className={`nav-item ${loc.pathname === n.path ? "active" : ""}`}
        >
          {n.label}
        </NavLink>
      ))}
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <Sidebar />
        <div className="content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="/predictions" element={<Predictions />} />
            <Route path="/data-sources" element={<DataSources />} />
            <Route path="/shadow-safety" element={<ShadowSafety />} />
            <Route path="/ops" element={<OpsConsole />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  );
}
