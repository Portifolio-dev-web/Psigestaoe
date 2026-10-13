import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { CheckCircle2, CreditCard, FlaskConical, Gift, Loader2 } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useConfirm } from "@/hooks/useConfirm";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  PLAN_LABEL, READ_ONLY_ALLOWED, READ_ONLY_REASON, daysLabel, formatBRL, formatDay, isTrustedCheckoutUrl,
} from "@/lib/subscription";
import BetaInterestDialog from "@/components/settings/BetaInterestDialog";

// ---------------------------------------------------------------------------
// PRODUTO (assinatura): Configurações > Assinatura.
//   - situação da conta, em uma frase (teste, assinante, atraso, somente
//     leitura, cortesia) e o que acontece quando o acesso pago acaba;
//   - planos mensal e anual. "Assinar" leva à página de pagamento DO MEIO DE
//     PAGAMENTO (nenhum dado de cartão passa pelo PsiGestão); na volta
//     (?retorno=1) a tela confere a confirmação por alguns segundos, porque o
//     aviso do meio de pagamento pode chegar depois da pessoa;
//   - cancelar a renovação (com confirmação: vale até o fim do período pago);
//   - só com o pagamento SIMULADO (desenvolvimento): ferramentas para ver cada
//     situação na tela sem esperar dias.
// ---------------------------------------------------------------------------
const SIM_EVENTS = [
  ["renovar", "Confirmar renovação"],
  ["atrasar", "Avisar atraso"],
  ["vencer_periodo", "Vencer o período pago"],
  ["vencer_tolerancia", "Vencer a tolerância"],
  ["encerrar_teste", "Encerrar o teste grátis"],
  ["cancelar", "Cancelar (aviso do meio)"],
  ["estornar", "Estornar pagamento"],
];
const RETURN_POLL_MS = 2000;
const RETURN_POLL_TRIES = 10;

function statusLine(sub) {
  switch (sub.state) {
    case "courtesy":
      return "Cortesia: acesso completo, sem cobrança.";
    case "trialing":
      return `Teste grátis até ${formatDay(sub.trial_ends_at)} (faltam ${daysLabel(sub.days_left)}). `
        + "Assine quando quiser: os dias de teste que sobrarem não se perdem.";
    case "active":
      return sub.cancel_at_period_end
        ? `Plano ${PLAN_LABEL[sub.plan] || ""} cancelado: acesso completo até ${formatDay(sub.paid_until)}. Depois, a conta fica somente leitura.`
        : `Plano ${PLAN_LABEL[sub.plan] || ""} ativo, pago até ${formatDay(sub.paid_until)}.`;
    case "past_due":
      return `Pagamento pendente. O acesso completo continua até ${formatDay(sub.grace_ends_at)} (${daysLabel(sub.days_left)}); `
        + "depois, a conta fica somente leitura.";
    default:
      return `Modo somente leitura: ${READ_ONLY_REASON[sub.reason] || "sem assinatura ativa"}. ${READ_ONLY_ALLOWED}`;
  }
}

export default function SubscriptionCard() {
  const { user, setUser, checkAuth } = useAuth();
  const sub = user?.subscription;
  const [searchParams, setSearchParams] = useSearchParams();
  const [confirm, confirmDialog] = useConfirm();
  const inFlight = useSingleFlight();
  const [checking, setChecking] = useState(false);
  // Versão beta: o plano escolhido em "Assinar" abre a janela de interesse (contato).
  const [interestPlan, setInterestPlan] = useState(null);
  const stateRef = useRef(sub?.state);
  stateRef.current = sub?.state;

  // Volta da página de pagamento: confere a confirmação por até 20 s.
  useEffect(() => {
    if (searchParams.get("retorno") !== "1") return undefined;
    const next = new URLSearchParams(searchParams);
    next.delete("retorno");
    setSearchParams(next, { replace: true });
    if (stateRef.current === "active") {
      toast.success("Assinatura ativa. Obrigado!");
      return undefined;
    }
    let tries = 0;
    let stopped = false;
    setChecking(true);
    const poll = async () => {
      await checkAuth();
      tries += 1;
      if (stopped) return;
      if (stateRef.current === "active") {
        setChecking(false);
        toast.success("Pagamento confirmado. Assinatura ativa!");
      } else if (tries >= RETURN_POLL_TRIES) {
        setChecking(false);
        toast.info("O pagamento ainda não foi confirmado. Boleto e Pix podem levar alguns minutos; a tela atualiza sozinha depois.");
      } else {
        setTimeout(poll, RETURN_POLL_MS);
      }
    };
    setTimeout(poll, RETURN_POLL_MS);
    return () => { stopped = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  if (!sub) return null;

  const subscribe = (plan) => inFlight.run(`checkout:${plan}`, async () => {
    try {
      const { data } = await api.post("/billing/checkout", { plan });
      // AS7: só a página de pagamento do meio (ou a simulação, no desenvolvimento).
      if (!isTrustedCheckoutUrl(data.url, sub.checkout_hosts)) {
        toast.error("Não foi possível abrir a página de pagamento. Tente de novo ou fale com o suporte (Ajuda).");
        return;
      }
      window.location.assign(data.url);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  const cancel = () => inFlight.run("cancel", async () => {
    const ok = await confirm({
      title: "Cancelar a renovação?",
      description: `Você continua com acesso completo até ${formatDay(sub.paid_until)}. Depois, a conta fica somente leitura: `
        + "dá para ver e exportar tudo, mas não registrar. Você pode assinar de novo quando quiser.",
      confirmLabel: "Cancelar renovação",
      cancelLabel: "Manter assinatura",
      destructive: true,
    });
    if (!ok) return;
    try {
      const { data } = await api.post("/billing/cancel", { confirm: true });
      setUser((current) => ({ ...current, ...data }));
      toast.success(`Renovação cancelada. Acesso completo até ${formatDay(data.subscription.paid_until)}.`);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  const simulate = (event) => inFlight.run(`sim:${event}`, async () => {
    try {
      const { data } = await api.post("/billing/simulate/event", { event });
      setUser((current) => ({ ...current, ...data }));
      toast.info(`Simulação aplicada: ${SIM_EVENTS.find(([id]) => id === event)[1].toLowerCase()}.`);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  const showPlans = sub.state !== "courtesy" && !(sub.state === "active" && !sub.cancel_at_period_end);
  const canCancel = sub.state !== "courtesy" && ["active", "past_due"].includes(sub.status) && !sub.cancel_at_period_end;
  const annualMonthly = Math.round(sub.prices.annual / 12);
  const annualSaving = sub.prices.monthly * 12 - sub.prices.annual;
  const plans = [
    { id: "monthly", title: "Mensal", price: formatBRL(sub.prices.monthly), unit: "por mês", note: "Cancele quando quiser." },
    {
      id: "annual", title: "Anual", price: formatBRL(sub.prices.annual), unit: "por ano",
      note: `Equivale a ${formatBRL(annualMonthly)} por mês${annualSaving > 0 ? ` (economia de ${formatBRL(annualSaving)})` : ""}.`,
    },
  ];

  return (
    <Card className="border-slate-200 p-6 shadow-sm" data-testid="subscription-card">
      <div className="flex flex-wrap items-center gap-2">
        <CreditCard className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Assinatura</h2>
        {sub.state === "courtesy" && (
          <Badge className="gap-1 bg-emerald-100 text-emerald-800 hover:bg-emerald-100"><Gift className="h-3 w-3" aria-hidden="true" /> Cortesia</Badge>
        )}
      </div>

      <p className="mt-3 text-sm text-slate-700" data-testid="subscription-status">{statusLine(sub)}</p>
      {checking && (
        <p className="mt-2 flex items-center gap-2 text-sm text-[#1E3A8A]" role="status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Confirmando o pagamento…
        </p>
      )}

      {showPlans && (
        <>
          {/* PRODUTO (versão beta): os preços aparecem, mas nada é cobrado pelo sistema;
              "Assinar" explica e recolhe o contato para a equipe (BetaInterestDialog). */}
          {sub.beta && (
            <p className="mt-4 rounded-md bg-blue-50 px-3 py-2 text-sm text-[#0F2C59]" role="note" data-testid="beta-note">
              <b>Versão beta:</b> os preços já estão definidos, mas a assinatura ainda não é feita pelo sistema e nada
              é cobrado. Toque em Assinar para deixar o seu contato: a equipe fala com você.
            </p>
          )}
          <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {plans.map((plan) => (
              <div key={plan.id} className="flex flex-col gap-3 rounded-lg border border-slate-200 p-4" data-testid={`plan-${plan.id}`}>
                <div>
                  <p className="font-head text-base font-bold text-[#0F172A]">{plan.title}</p>
                  <p className="mt-1"><span className="font-head text-2xl font-extrabold text-[#0F172A]">{plan.price}</span>{" "}
                    <span className="text-sm text-slate-600">{plan.unit}</span></p>
                  <p className="mt-1 text-xs text-slate-600">{plan.note}</p>
                </div>
                <Button onClick={() => (sub.beta ? setInterestPlan(plan.id) : subscribe(plan.id))}
                  disabled={inFlight.isBusy(`checkout:${plan.id}`)}
                  className="mt-auto bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid={`subscribe-${plan.id}`}>
                  {inFlight.isBusy(`checkout:${plan.id}`) ? <Loader2 className="h-4 w-4 animate-spin" /> : `Assinar ${plan.title.toLowerCase()}`}
                </Button>
              </div>
            ))}
          </div>
          {!sub.beta && !sub.checkout_available && (
            <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900" role="note">
              O pagamento on-line ainda não está disponível. Para assinar, fale com o suporte (página de Ajuda).
            </p>
          )}
          <ul className="mt-4 space-y-1 text-xs text-slate-600">
            {!sub.beta && (
              <li className="flex gap-2"><CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" aria-hidden="true" />
                O pagamento é feito na página segura do meio de pagamento; o PsiGestão não recebe os dados do seu cartão.</li>
            )}
            <li className="flex gap-2"><CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" aria-hidden="true" />
              Se a assinatura acabar, nada é apagado: {READ_ONLY_ALLOWED.charAt(0).toLowerCase() + READ_ONLY_ALLOWED.slice(1)}</li>
          </ul>
        </>
      )}

      {canCancel && (
        <div className="mt-5 border-t border-slate-100 pt-4">
          <Button variant="outline" onClick={cancel} disabled={inFlight.isBusy("cancel")}
            className="border-red-200 text-red-700 hover:bg-red-50" data-testid="cancel-subscription">
            Cancelar assinatura
          </Button>
        </div>
      )}

      {/* Só no desenvolvimento (pagamento simulado): ver cada situação sem esperar dias. */}
      {sub.simulated && (
        <div className="mt-6 rounded-lg border border-dashed border-slate-300 p-4" data-testid="billing-sim-tools">
          <p className="flex items-center gap-2 text-sm font-medium text-slate-700">
            <FlaskConical className="h-4 w-4" aria-hidden="true" /> Ferramentas de teste (pagamento simulado)
          </p>
          <p className="mt-1 text-xs text-slate-600">Aparecem só no desenvolvimento. Em produção, a API recusa o pagamento simulado.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {SIM_EVENTS.map(([id, label]) => (
              <Button key={id} type="button" size="sm" variant="outline" onClick={() => simulate(id)}
                disabled={inFlight.isBusy(`sim:${id}`)} className="border-slate-300" data-testid={`sim-${id}`}>
                {label}
              </Button>
            ))}
          </div>
        </div>
      )}
      {confirmDialog}
      {interestPlan && (
        <BetaInterestDialog plan={interestPlan} prices={sub.prices} onClose={() => setInterestPlan(null)} />
      )}
    </Card>
  );
}
