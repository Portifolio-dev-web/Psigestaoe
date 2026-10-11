import { Link } from "react-router-dom";
import { Card } from "@/components/ui/card";
import PublicPageShell from "@/components/PublicPageShell";
import { PolicySections, useTermsOfUse } from "@/components/PrivacyPolicyContent";

// Página PÚBLICA dos Termos de Uso (rota /termos). Análise da administração
// (AD1): antes não existiam Termos de Uso, e quem pagava não tinha como saber
// as regras do serviço (planos, uso permitido, suspensão). Abre sem login, como
// a política: quem vai se cadastrar lê ANTES de aceitar.
export default function TermsOfUse() {
  const { policy, error } = useTermsOfUse();

  return (
    <PublicPageShell>
      <Card className="border-slate-200 p-6 shadow-sm sm:p-10">
        <h1 className="font-head text-2xl font-extrabold tracking-tight text-[#0F172A] sm:text-3xl">
          Termos de Uso
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          Como os dados pessoais são tratados está na{" "}
          <Link to="/privacidade" className="font-medium text-[#1E3A8A] underline underline-offset-2">Política de Privacidade</Link>.
        </p>
        <div className="mt-6">
          <PolicySections policy={policy} error={error} documentName="os Termos de Uso" idPrefix="terms" />
        </div>
      </Card>
    </PublicPageShell>
  );
}
