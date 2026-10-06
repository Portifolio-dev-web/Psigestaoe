import { useNavigate } from "react-router-dom";
import { ArrowLeft, Stethoscope } from "lucide-react";

// ---------------------------------------------------------------------------
// Moldura das páginas que abrem SEM login: Política de Privacidade e, desde a
// análise de UI (O2), a Ajuda. Logo, "Voltar" e a região principal (<main>).
// Antes este layout morava dentro de pages/PrivacyPolicy.js; a Ajuda precisa
// do mesmo, e duas cópias acabariam diferentes.
// ---------------------------------------------------------------------------
export default function PublicPageShell({ children }) {
  const navigate = useNavigate();

  // Aberta em nova aba (a partir do cadastro) não há histórico para voltar.
  const goBack = () => (window.history.length > 1 ? navigate(-1) : navigate("/"));

  // Acessibilidade (complementares): <main> como região principal (antes a
  // página pública não tinha, e o leitor de tela não tinha como pular para ela).
  return (
    // tabIndex -1: recebe o foco ao trocar de tela (análise de UI, A1, RouteAnnouncer).
    <main tabIndex={-1} className="min-h-screen bg-[#F8FAFC] px-4 py-8 focus:outline-none sm:py-12">
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#1E3A8A]">
              <Stethoscope className="h-5 w-5 text-white" aria-hidden="true" />
            </div>
            <span className="font-head text-lg font-bold text-[#0F172A]">PsiGestão</span>
          </div>
          <button onClick={goBack} className="flex min-h-6 items-center gap-1.5 text-sm text-slate-500 hover:text-[#1E3A8A]">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Voltar
          </button>
        </div>
        {children}
      </div>
    </main>
  );
}
