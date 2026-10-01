import { useRef } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import Login from "@/pages/Login";
import Register from "@/pages/Register";
import Dashboard from "@/pages/Dashboard";
import Patients from "@/pages/Patients";
import PatientDetail from "@/pages/PatientDetail";
import Agenda from "@/pages/Agenda";
import Settings from "@/pages/Settings";
import PrivacyPolicy from "@/pages/PrivacyPolicy";
import AppLayout from "@/components/AppLayout";
import TermsGate from "@/components/TermsGate";
import LockScreen from "@/components/LockScreen";
import { IdleLockProvider } from "@/context/IdleLockContext";
import { Loader2 } from "lucide-react";

function LoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F8FAFC]">
      <Loader2 className="h-8 w-8 animate-spin text-[#1E3A8A]" />
    </div>
  );
}

// O AuthCallback antigo foi totalmente removido daqui,
// assim como os imports do useRef e useLocation, e do api, que não são mais necessários neste nível.

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  // Se o sistema já foi exibido nesta aba, um bloqueio NÃO o desmonta (ver abaixo).
  const appMountedRef = useRef(false);

  if (loading) return <LoadingScreen />;
  // PRODUTO: lembra a página de origem; depois do login a pessoa volta para
  // ela (ex.: sessão expirada no meio de uma anotação -> volta à ficha, onde
  // o rascunho está esperando).
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  // Sessão já bloqueada ao abrir/recarregar a página: não há o que preservar,
  // então só a tela de bloqueio (o servidor recusaria os dados de qualquer jeito).
  if (user.locked && !appMountedRef.current) return <LockScreen />;
  // Conformidade (item 16): sem o aceite da versão VIGENTE da Política de
  // Privacidade, nenhuma tela do sistema é exibida, só a tela de aceite.
  // O backend aplica a mesma regra (get_active_user), então isto é a
  // experiência do usuário, não a única barreira.
  if (!user.terms_accepted) return <TermsGate />;

  appMountedRef.current = true;
  const locked = Boolean(user.locked);
  // PRODUTO (bloqueio por inatividade): bloqueio DURANTE o uso. O sistema
  // continua montado por baixo (nada do que estava aberto se perde), mas
  // invisível e `inert`: sem foco de teclado, cliques ou leitor de tela.
  return (
    <IdleLockProvider>
      <div inert={locked} aria-hidden={locked || undefined} className={locked ? "invisible" : undefined}>
        {children}
      </div>
      {locked && <LockScreen />}
    </IdleLockProvider>
  );
}

function PublicOnly({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <LoadingScreen />;
  if (user) {
    // Volta para onde a pessoa estava quando precisou entrar de novo.
    const from = location.state?.from;
    return <Navigate to={from ? `${from.pathname}${from.search || ""}` : "/dashboard"} replace />;
  }
  return children;
}

function AppRouter() {
  // A interceção do location.hash foi removida, o router agora é muito mais limpo.
  return (
    <Routes>
      <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
      <Route path="/register" element={<PublicOnly><Register /></PublicOnly>} />
      {/* Pública para todos (logado ou não): a política deve poder ser lida antes do aceite. */}
      <Route path="/privacidade" element={<PrivacyPolicy />} />
      <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/pacientes" element={<Patients />} />
        <Route path="/pacientes/:id" element={<PatientDetail />} />
        <Route path="/agenda" element={<Agenda />} />
        <Route path="/configuracoes" element={<Settings />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <AuthProvider>
          <AppRouter />
          <Toaster position="top-right" richColors />
        </AuthProvider>
      </BrowserRouter>
    </div>
  );
}

export default App;