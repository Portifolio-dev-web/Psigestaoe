import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";
import PublicPageShell from "@/components/PublicPageShell";

// ---------------------------------------------------------------------------
// DEPLOY (varredura, item D2): antes, qualquer endereço inválido caía direto
// no Dashboard (App.js: Navigate to="/dashboard"), sem explicação — um link
// quebrado ou um favorito antigo parecia bug, não "esta página não existe".
//
// Mesma moldura das páginas públicas (PublicPageShell, com <main> e "Voltar"):
// funciona tanto para quem está logado quanto para quem não está, sem
// precisar saber qual é o caso — por isso a rota fica FORA do AppBranch
// (App.js), como /privacidade e /termos. "Ir para o início" manda sempre
// para /dashboard: quem está logado chega lá; quem não está, o próprio
// /dashboard redireciona para /login (ProtectedRoute cuida disso sozinho).
// ---------------------------------------------------------------------------
export default function NotFound() {
  return (
    <PublicPageShell>
      <div className="flex flex-col items-center py-10 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-[#F0F4F8] text-[#1E3A8A]">
          <Compass className="h-7 w-7" aria-hidden="true" />
        </div>
        <h1 className="mt-5 font-head text-2xl font-bold text-[#0F172A]">Página não encontrada</h1>
        <p className="mt-2 max-w-sm text-sm leading-relaxed text-slate-500">
          O endereço pode ter mudado, ou o link usado está incompleto. Confira se digitou certo, ou volte para o início.
        </p>
        <Button asChild className="mt-6 bg-[#1E3A8A] hover:bg-[#0F2C59]">
          <Link to="/dashboard">Ir para o início</Link>
        </Button>
      </div>
    </PublicPageShell>
  );
}
