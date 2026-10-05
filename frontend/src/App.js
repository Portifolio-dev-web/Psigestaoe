import { Suspense, lazy, useRef } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import AppLayout from "@/components/AppLayout";
import TermsGate from "@/components/TermsGate";
import LockScreen from "@/components/LockScreen";
import RouteAnnouncer from "@/components/RouteAnnouncer";
import { IdleLockProvider } from "@/context/IdleLockContext";
import { Loader2 } from "lucide-react";

// Complementares (desempenho): cada tela é carregada só quando é aberta
// (React.lazy). Antes o app inteiro vinha num único arquivo de ~250 KB
// (compactado), inclusive para quem só abria a tela de login. O que é comum
// a todas (layout, bloqueio, aceite da política) continua no arquivo principal.
const Login = lazy(() => import("@/pages/Login"));
const Register = lazy(() => import("@/pages/Register"));
const ForgotPassword = lazy(() => import("@/pages/ForgotPassword"));
const EmailLinkPassword = lazy(() => import("@/pages/EmailLinkPassword"));
const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Patients = lazy(() => import("@/pages/Patients"));
const PatientDetail = lazy(() => import("@/pages/PatientDetail"));
const Agenda = lazy(() => import("@/pages/Agenda"));
const Settings = lazy(() => import("@/pages/Settings"));
const PrivacyPolicy = lazy(() => import("@/pages/PrivacyPolicy"));

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
  // Suspense: enquanto o arquivo da tela chega, mostra o mesmo carregamento do login.
  return (
    <Suspense fallback={<LoadingScreen />}>
    <Routes>
      <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
      <Route path="/register" element={<PublicOnly><Register /></PublicOnly>} />
      {/* Segurança média: "Esqueci minha senha" e as telas abertas pelos links
          do e-mail. Os links valem mesmo com alguém logado no navegador (por
          isso não ficam dentro de PublicOnly, que redirecionaria ao painel). */}
      <Route path="/esqueci-senha" element={<PublicOnly><ForgotPassword /></PublicOnly>} />
      <Route path="/criar-senha" element={<EmailLinkPassword purpose="signup" />} />
      <Route path="/redefinir-senha" element={<EmailLinkPassword purpose="password_reset" />} />
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
    </Suspense>
  );
}

function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <AuthProvider>
          {/* Acessibilidade (análise de UI, A1): título, foco e anúncio a cada troca de tela. */}
          <RouteAnnouncer />
          <AppRouter />
          <Toaster position="top-right" richColors />
        </AuthProvider>
      </BrowserRouter>
    </div>
  );
}

export default App;