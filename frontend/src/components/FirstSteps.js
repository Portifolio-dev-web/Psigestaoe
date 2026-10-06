import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight, CheckCircle2, Circle, Loader2, Rocket } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

// ---------------------------------------------------------------------------
// "Primeiros passos" no painel (análise de UI, O1).
//
// Uma conta nova caía num painel vazio, sem saber por onde começar. Esta lista
// mostra o que deixa o consultório pronto, cada passo com o link direto:
//   1. revisar o termo de consentimento (o texto que os pacientes aceitam);
//   2. ativar a verificação em duas etapas (só para quem entra com senha;
//      quem entra pelo Google usa a verificação da conta Google);
//   3. cadastrar o primeiro paciente;
//   4. marcar a primeira sessão.
// Cada passo se marca SOZINHO quando é feito (os dados vêm de /auth/me e de
// /dashboard/stats). A lista some quando tudo está feito, ou em "Ocultar"
// (gravado na conta; a página de Ajuda traz de volta).
// ---------------------------------------------------------------------------

export function firstSteps(user, onboarding) {
  return [
    {
      id: "termo", done: Boolean(user?.consent_term_reviewed), to: "/configuracoes?aba=termo",
      label: "Revise o termo de consentimento",
      description: "É o texto que seus pacientes aceitam. Ajuste ao seu atendimento ou mantenha o modelo.",
    },
    // Quem só entra pelo Google não tem senha para proteger com o segundo fator daqui.
    ...(user?.has_password ? [{
      id: "duas-etapas", done: Boolean(user?.mfa_enabled), to: "/configuracoes?aba=conta",
      label: "Ative a verificação em duas etapas",
      description: "Além da senha, um código do celular: uma senha vazada, sozinha, não abre os prontuários.",
    }] : []),
    {
      id: "paciente", done: Boolean(onboarding?.has_patients), to: "/pacientes?novo=1",
      label: "Cadastre o primeiro paciente",
      description: "Com o consentimento registrado. Pacientes menores de idade pedem os dados do responsável.",
    },
    {
      id: "sessao", done: Boolean(onboarding?.has_sessions), to: "/agenda?nova=1",
      label: "Marque a primeira sessão",
      description: "Atendimentos semanais podem ser marcados de uma vez, em “Repetir”.",
    },
  ];
}

export default function FirstSteps({ onboarding }) {
  const { user, setUser } = useAuth();
  const [hiding, setHiding] = useState(false);
  const steps = firstSteps(user, onboarding);
  const doneCount = steps.filter((step) => step.done).length;

  if (!user || !onboarding || user.onboarding_dismissed || doneCount === steps.length) return null;

  const hide = async () => {
    setHiding(true);
    try {
      const { data } = await api.put("/auth/onboarding", { dismissed: true });
      setUser((current) => ({ ...current, ...data }));
      toast.success("Primeiros passos ocultados. Para vê-los de novo, use a página de Ajuda.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      setHiding(false);
    }
  };

  return (
    <Card className="border-blue-200 bg-blue-50/40 p-6 shadow-sm" data-testid="first-steps">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Rocket className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
          <h2 className="font-head text-lg font-bold text-[#0F172A]">Primeiros passos</h2>
        </div>
        <p className="text-sm text-slate-600" data-testid="first-steps-progress">
          {doneCount} de {steps.length} concluídos
        </p>
      </div>
      {/* Barra de progresso só visual: o texto acima já diz o mesmo ao leitor de tela. */}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-blue-100" aria-hidden="true">
        <div className="h-full rounded-full bg-[#1E3A8A] transition-all" style={{ width: `${(doneCount / steps.length) * 100}%` }} />
      </div>
      <ol className="mt-4 space-y-2">
        {steps.map((step) => (
          <li key={step.id} data-testid={`first-step-${step.id}`}>
            {step.done ? (
              <div className="flex items-start gap-3 rounded-md px-3 py-2">
                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" aria-hidden="true" />
                <p className="text-sm text-slate-600">
                  {step.label} <span className="font-medium text-emerald-700">· Concluído</span>
                </p>
              </div>
            ) : (
              <Link to={step.to}
                className="group flex items-start gap-3 rounded-md border border-blue-100 bg-white px-3 py-2.5 transition-colors hover:border-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]">
                <Circle className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-[#0F172A]">{step.label}</span>
                  <span className="block text-xs leading-relaxed text-slate-600">{step.description}</span>
                </span>
                <ArrowRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-400 group-hover:text-[#1E3A8A]" aria-hidden="true" />
              </Link>
            )}
          </li>
        ))}
      </ol>
      <div className="mt-3 flex justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={hide} disabled={hiding} className="text-[#1E3A8A]"
          data-testid="first-steps-hide">
          {hiding && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />} Ocultar primeiros passos
        </Button>
      </div>
    </Card>
  );
}
