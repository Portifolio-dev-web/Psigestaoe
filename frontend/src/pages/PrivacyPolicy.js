import { Card } from "@/components/ui/card";
import PublicPageShell from "@/components/PublicPageShell";
import { PolicySections, usePrivacyPolicy } from "@/components/PrivacyPolicyContent";

// Página PÚBLICA da Política de Privacidade (rota /privacidade).
// Precisa abrir sem login: quem vai se cadastrar tem o direito de ler a
// política ANTES de aceitá-la (LGPD, art. 9º). O cadastro, o login e a tela
// de aceite apontam para cá.
// Análise de UI (O2): a moldura (logo, "Voltar", <main>) passou para
// components/PublicPageShell.js, compartilhada com a página de Ajuda.
export default function PrivacyPolicy() {
  const { policy, error } = usePrivacyPolicy();

  return (
    <PublicPageShell>
      <Card className="border-slate-200 p-6 shadow-sm sm:p-10">
        <h1 className="font-head text-2xl font-extrabold tracking-tight text-[#0F172A] sm:text-3xl">
          Política de Privacidade
        </h1>
        <div className="mt-6">
          <PolicySections policy={policy} error={error} />
        </div>
      </Card>
    </PublicPageShell>
  );
}
