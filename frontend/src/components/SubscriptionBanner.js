import { Link } from "react-router-dom";
import { Clock, CreditCard, Lock } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { READ_ONLY_ALLOWED, READ_ONLY_REASON, SUBSCRIPTION_PATH, daysLabel, formatDay } from "@/lib/subscription";

// ---------------------------------------------------------------------------
// PRODUTO (assinatura): faixa no topo de todas as telas do sistema.
//   - teste grátis: discreta (azul); nos últimos 3 dias, em destaque (âmbar);
//   - pagamento atrasado (tolerância): âmbar, com a data-limite;
//   - somente leitura: o motivo, o que continua liberado e "Assinar";
//   - cancelada (ainda dentro do período pago): até quando vale.
// Assinante em dia e cortesia não veem faixa nenhuma.
// Texto, ícone e cor dizem a mesma coisa: a situação não depende só da cor
// (acessibilidade, WCAG 1.4.1). Não é região "ao vivo": aparece com a tela.
// ---------------------------------------------------------------------------
const TONES = {
  info: "border-blue-200 bg-blue-50 text-[#0F2C59]",
  warn: "border-amber-300 bg-amber-50 text-amber-900",
  block: "border-red-200 bg-red-50 text-red-900",
};

function banner(sub) {
  if (!sub) return null;
  if (sub.state === "trialing") {
    const ending = sub.days_left <= 3;
    return {
      tone: ending ? "warn" : "info", icon: Clock, action: "Ver planos",
      text: sub.days_left === 0 ? "Seu teste grátis termina hoje."
        : `Teste grátis: ${ending ? "só " : ""}faltam ${daysLabel(sub.days_left)} (até ${formatDay(sub.trial_ends_at)}).`,
    };
  }
  if (sub.state === "past_due") {
    return {
      tone: "warn", icon: CreditCard, action: "Regularizar",
      text: `Pagamento pendente. Seu acesso completo continua até ${formatDay(sub.grace_ends_at)} `
        + `(${daysLabel(sub.days_left)}).`,
    };
  }
  if (sub.state === "read_only") {
    return {
      tone: "block", icon: Lock, action: "Assinar",
      text: `Modo somente leitura: ${READ_ONLY_REASON[sub.reason] || "sem assinatura ativa"}. ${READ_ONLY_ALLOWED}`,
    };
  }
  if (sub.state === "active" && sub.cancel_at_period_end) {
    return {
      tone: "info", icon: CreditCard, action: "Assinar de novo",
      text: `Assinatura cancelada: acesso completo até ${formatDay(sub.paid_until)}.`,
    };
  }
  return null;
}

export default function SubscriptionBanner() {
  const { user } = useAuth();
  const content = banner(user?.subscription);
  if (!content) return null;
  const Icon = content.icon;
  return (
    <div className={`mb-5 flex flex-col gap-2 rounded-lg border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between ${TONES[content.tone]}`}
      data-testid="subscription-banner" data-state={user.subscription.state}>
      <p className="flex items-start gap-2">
        <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{content.text}</span>
      </p>
      <Link to={SUBSCRIPTION_PATH}
        className="inline-flex min-h-8 shrink-0 items-center self-start rounded-md bg-[#1E3A8A] px-3 text-sm font-medium text-white hover:bg-[#0F2C59] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A] focus-visible:ring-offset-2 sm:self-auto"
        data-testid="subscription-banner-action">
        {content.action}
      </Link>
    </div>
  );
}
