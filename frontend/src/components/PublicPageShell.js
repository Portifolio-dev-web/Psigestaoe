import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
// DESIGN (prévia validada): logo completa (símbolo + nome + legenda), recorte
// do arquivo original (logo-transparente.png) do usuário — aqui o fundo é
// claro, então o texto escuro do arquivo original tem contraste bom (ao
// contrário do navy da landing/login/menu, que usam só o símbolo).
import logoCompleto from "@/assets/logo-completo.png";

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
          <img src={logoCompleto} alt="PsiGestão" className="h-8 w-auto" />
          <button onClick={goBack} className="flex min-h-6 items-center gap-1.5 text-sm text-slate-500 hover:text-[#1E3A8A]">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Voltar
          </button>
        </div>
        {children}
      </div>
    </main>
  );
}
