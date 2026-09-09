import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { ActivityProvider } from './context/ActivityContext';
import { AssistantProvider } from './context/AssistantContext';
import { ToastProvider } from './context/ToastContext';
import { LayoutProvider, useLayout } from './context/LayoutContext';
import { CensusProvider } from './context/CensusContext';
import { INSTRUMENT_MANAGER_ROLES } from './config/roles';
import ProtectedRoute from './components/ProtectedRoute';
import Sidebar from './components/Sidebar';
import AppHeader from './components/AppHeader';
import RightRail from './components/RightRail';
import AssistantPanel from './components/AssistantPanel';
import Login from './pages/Login';
import Signup from './pages/Signup';
import Dashboard from './pages/Dashboard';
import Children from './pages/Children';
import Report from './pages/Report';
import ChildProgressReport from './pages/ChildProgressReport';
import Monitoring from './pages/Monitoring';
import AgencySummary from './pages/AgencySummary';
import Settings from './pages/Settings';
import Users from './pages/Users';
import MyProfile from './pages/MyProfile';
import Instruments from './pages/Instruments';
import PreAssessment from './pages/PreAssessment';
import Schedule from './pages/Schedule';
import Survey from './pages/Survey';
import SamdReadiness from './pages/SamdReadiness';
import AdoptionTracker from './pages/AdoptionTracker';
import AdoptionCase from './pages/AdoptionCase';

/* The three-column shell.
 *
 * Chrome across the top, then a row of up to three columns on the app's own
 * ground: every destination on the left, the work in the middle, ambient
 * context on the right. How many of those columns exist is decided by
 * LayoutContext from the measured window width — see the note there for why
 * that is JavaScript and not a media query.
 *
 * The padding and the gap live HERE, not inside the screens. A screen that
 * pads itself cannot be put next to a rail without the two disagreeing about
 * the margin, which is how the old layout ended up with three different
 * gutters on three different pages.
 */
function Shell({ children }) {
  const layout = useLayout();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--bg-app)', overflow: 'hidden' }}>
      <AppHeader />
      <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: layout.gap, padding: layout.pad, overflow: 'hidden' }}>
        {layout.leftRailOn && <Sidebar />}
        <main className="racco-scroll" style={{ flex: 1, minWidth: 0, overflowX: 'hidden', overflowY: 'auto', paddingRight: 2 }}>
          {children}
        </main>
        {layout.rightRailOn && <RightRail />}
      </div>
      {/* Every protected screen. Fixed-position, so it sits outside the
          scrolling main rather than moving with the page. */}
      <AssistantPanel />
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
      <ActivityProvider>
      <AssistantProvider>
        <BrowserRouter>
          {/* Both providers sit ABOVE the routes: the census feeds the right
              rail and the Dashboard from one request, and remounting it per
              route would re-fetch it on every click. */}
          <LayoutProvider>
          <CensusProvider>
          <Routes>
          <Route path="/login" element={<Login />} />
          {/* Open, like /login: someone without an account has to be able
              to reach it. It creates a request, never access. */}
          <Route path="/signup" element={<Signup />} />
          {/* Public, token-gated child opinionnaire (opened via QR code). */}
          <Route path="/survey/:token" element={<Survey />} />
          <Route path="/" element={<ProtectedRoute><Shell><Dashboard /></Shell></ProtectedRoute>} />
          {/* Terminated-case archive lives inside Records (Archived filter) — no separate route. */}
          <Route path="/children" element={<ProtectedRoute roles={['Administrator', 'Staff', 'Psychologist']}><Shell><Children /></Shell></ProtectedRoute>} />
          {/* The adoption process module: a second lifecycle that picks a
              child up when the psychologist marks the assessment complete.
              Casework, so psychologists are not routed here at all. */}
          <Route path="/adoption" element={<ProtectedRoute roles={['Administrator', 'Staff']}><Shell><AdoptionTracker /></Shell></ProtectedRoute>} />
          <Route path="/adoption/case/:id" element={<ProtectedRoute roles={['Administrator', 'Staff']}><Shell><AdoptionCase /></Shell></ProtectedRoute>} />
          <Route path="/instruments" element={<ProtectedRoute roles={INSTRUMENT_MANAGER_ROLES}><Shell><Instruments /></Shell></ProtectedRoute>} />
          <Route path="/pre-assessment" element={<ProtectedRoute roles={['Psychologist']}><Shell><PreAssessment /></Shell></ProtectedRoute>} />
          <Route path="/schedule" element={<ProtectedRoute roles={['Administrator', 'Psychologist', 'Staff']}><Shell><Schedule /></Shell></ProtectedRoute>} />
          <Route path="/reports" element={<ProtectedRoute><Shell><Report /></Shell></ProtectedRoute>} />
          <Route path="/report/child/:id" element={<ProtectedRoute><Shell><ChildProgressReport /></Shell></ProtectedRoute>} />
          <Route path="/monitoring" element={<ProtectedRoute roles={['Administrator', 'Staff', 'Psychologist']}><Shell><Monitoring /></Shell></ProtectedRoute>} />
          <Route path="/reports/summary" element={<ProtectedRoute roles={['Administrator', 'Staff']}><Shell><AgencySummary /></Shell></ProtectedRoute>} />
          <Route path="/users" element={<ProtectedRoute roles={['Administrator']}><Shell><Users /></Shell></ProtectedRoute>} />
          <Route path="/samd" element={<ProtectedRoute roles={['Administrator']}><Shell><SamdReadiness /></Shell></ProtectedRoute>} />
          <Route path="/settings" element={<ProtectedRoute roles={['Administrator']}><Shell><Settings /></Shell></ProtectedRoute>} />
          {/* Demo-only profile prototype for Social Worker / Psychologist. */}
          <Route path="/profile" element={<ProtectedRoute roles={['Staff', 'Psychologist']}><Shell><MyProfile /></Shell></ProtectedRoute>} />
          {/* Anything unmatched — a stale bookmark, a typo, a link to a page a
              role cannot reach — lands on sign-in rather than rendering nothing. */}
          <Route path="*" element={<Navigate to="/login" replace />} />
          </Routes>
          </CensusProvider>
          </LayoutProvider>
        </BrowserRouter>
      </AssistantProvider>
      </ActivityProvider>
      </ToastProvider>
    </AuthProvider>
  );
}
