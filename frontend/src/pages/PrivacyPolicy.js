import { useNavigate } from "react-router-dom";
import { ArrowLeft, Stethoscope } from "lucide-react";
import { Card } from "@/components/ui/card";
import { PolicySections, usePrivacyPolicy } from "@/components/PrivacyPolicyContent";

// Página PÚBLICA da Política de Privacidade (rota /privacidade).
// Precisa abrir sem login: quem vai se cadastrar tem o direito de ler a
// política ANTES de aceitá-la (LGPD, art. 9º). O cadastro, o login e a tela
// de aceite apontam para cá.
export default function PrivacyPolicy() {
  const navigate = useNavigate();
  const { policy, error } = usePrivacyPolicy();

  // Aberta em nova aba (a partir do cadastro) não há histórico para voltar.
  const goBack = () => (window.history.length > 1 ? navigate(-1) : navigate("/"));

  // Acessibilidade (complementares): <main> como região principal (antes a
  // página pública não tinha, e o leitor de tela não tinha como pular para ela).
  return (
    <main className="min-h-screen bg-[#F8FAFC] px-4 py-8 sm:py-12">
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#1E3A8A]">
              <Stethoscope className="h-5 w-5 text-white" />
            </div>
            <span className="font-head text-lg font-bold text-[#0F172A]">PsiGestão</span>
          </div>
          <button onClick={goBack} className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-[#1E3A8A]">
            <ArrowLeft className="h-4 w-4" /> Voltar
          </button>
        </div>

        <Card className="border-slate-200 p-6 shadow-sm sm:p-10">
          <h1 className="font-head text-2xl font-extrabold tracking-tight text-[#0F172A] sm:text-3xl">
            Política de Privacidade
          </h1>
          <div className="mt-6">
            <PolicySections policy={policy} error={error} />
          </div>
        </Card>
      </div>
    </main>
  );
}
