import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { formatDay } from "@/lib/subscription";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// PRODUTO (administração): o diálogo de CADA ação da tela de administração.
//
// Toda ação explica o que vai acontecer ANTES de acontecer, diz que a pessoa
// recebe um e-mail e pede a confirmação do administrador: a senha atual (ou,
// para quem só entra pelo Google, o código do aplicativo autenticador). Quem
// decide é o servidor (server.py, seção "ADMINISTRAÇÃO"): a tela só ajuda a
// não errar (ex.: motivo da suspensão obrigatório já aqui).
// ---------------------------------------------------------------------------
export const ADMIN_ACTIONS = {
  grant: {
    title: "Conceder cortesia",
    endpoint: "courtesy",
    body: { grant: true },
    effect: "Libera todos os recursos sem cobrança, sem data para acabar, até você revogar.",
    reason: { label: "Motivo (opcional)", max: 120, placeholder: "Ex.: usuária fundadora" },
    submit: "Conceder cortesia",
    done: "Cortesia concedida.",
  },
  revoke: {
    title: "Revogar cortesia",
    endpoint: "courtesy",
    body: { grant: false },
    effect: "A conta volta às regras normais da assinatura. Sem um plano ativo, fica em somente leitura "
      + "(vê e exporta tudo, mas não registra nada novo).",
    reason: { label: "Motivo (opcional)", max: 200 },
    submit: "Revogar cortesia",
    destructive: true,
    done: "Cortesia revogada.",
  },
  trial: {
    title: "Prorrogar teste grátis",
    endpoint: "trial",
    effect: "Soma dias ao fim atual do teste (ou a partir de hoje, se ele já acabou).",
    days: true,
    reason: { label: "Motivo (opcional)", max: 200, placeholder: "Ex.: pediu mais tempo para conhecer" },
    submit: "Prorrogar teste",
    done: "Teste grátis prorrogado.",
  },
  endSessions: {
    title: "Encerrar sessões",
    endpoint: "end-sessions",
    effect: "Desconecta a conta de todos os aparelhos agora. A pessoa entra de novo com a senha. "
      + "Use em suspeita de senha vazada ou de aparelho perdido.",
    reason: { label: "Motivo (opcional, vai no e-mail)", max: 200, placeholder: "Ex.: acesso estranho relatado por ela" },
    submit: "Encerrar sessões",
    destructive: true,
    done: "Sessões encerradas.",
  },
  suspend: {
    title: "Suspender conta",
    endpoint: "suspension",
    body: { suspend: true },
    effect: "A pessoa sai de todos os aparelhos e não consegue mais entrar; os links de cadastro de pacientes dela "
      + "param de funcionar. Os dados e prontuários continuam guardados, sem nenhuma alteração. Enquanto estiver "
      + "suspensa, ela não acessa os prontuários: use só em caso de fraude ou abuso.",
    reason: { label: "Motivo da suspensão", max: 200, min: 5, required: true, placeholder: "Ex.: uso indevido da conta" },
    submit: "Suspender conta",
    destructive: true,
    done: "Conta suspensa.",
  },
  reactivate: {
    title: "Reativar conta",
    endpoint: "suspension",
    body: { suspend: false },
    effect: "A pessoa volta a entrar normalmente, com tudo como estava.",
    reason: { label: "Observação (opcional)", max: 200 },
    submit: "Reativar conta",
    done: "Conta reativada.",
  },
};

const DAY_MS = 86400000;

export default function AdminActionDialog({ action, account, hasPassword, trialOptions, onClose, onDone }) {
  const config = ADMIN_ACTIONS[action];
  const [reason, setReason] = useState("");
  const [days, setDays] = useState(String(trialOptions?.[0] || 7));
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const confirmRef = useRef(null);

  // Cada abertura começa limpa (a senha digitada antes nunca fica no formulário).
  useEffect(() => {
    setReason("");
    setConfirm("");
    setErrors({});
    setDays(String(trialOptions?.[0] || 7));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, account?.user_id]);

  if (!config || !account) return null;
  const who = account.name ? `${account.name} (${account.email})` : account.email;

  // Prorrogar: mostra a data nova antes de confirmar.
  const trialEnd = account.subscription?.trial_ends_at ? Date.parse(account.subscription.trial_ends_at) : Date.now();
  const newTrialEnd = new Date(Math.max(Date.now(), trialEnd) + Number(days) * DAY_MS).toISOString();

  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (config.reason?.required && reason.trim().length < (config.reason.min || 1)) {
      next.reason = "Escreva o motivo (pelo menos 5 letras). A pessoa recebe esse texto por e-mail.";
    }
    if (!confirm) next.confirm = hasPassword ? "Digite a sua senha." : "Digite o código do aplicativo.";
    setErrors(next);
    if (next.reason) {
      document.getElementById("admin-reason")?.focus();
      return;
    }
    if (next.confirm) {
      confirmRef.current?.focus();
      return;
    }
    setSaving(true);
    try {
      const body = { ...(config.body || {}), reason: reason.trim(), confirm, ...(config.days ? { days: Number(days) } : {}) };
      const { data } = await api.post(`/admin/users/${account.user_id}/${config.endpoint}`, body);
      onDone(data, config.done);
    } catch (err) {
      const detail = err.response?.data?.detail;
      const message = formatApiErrorDetail(detail) || err.message;
      // Confirmação errada: o erro fica no campo, que é limpo e recebe o foco.
      if (detail?.code === "admin_confirm_invalid" || err.response?.status === 429) {
        setErrors({ confirm: message });
        setConfirm("");
        requestAnimationFrame(() => confirmRef.current?.focus());
      } else {
        setErrors({ form: message });
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}>
      <DialogContent className="sm:max-w-lg" data-testid="admin-action-dialog">
        <DialogHeader>
          <DialogTitle className="font-head">{config.title}</DialogTitle>
          <DialogDescription>
            Conta: <b className="font-medium text-[#0F172A]">{who}</b>
          </DialogDescription>
        </DialogHeader>
        {/* noValidate: os erros aparecem no próprio formulário (padrão do sistema). */}
        <form onSubmit={submit} noValidate className="space-y-4">
          <div className={`rounded-md p-3 text-sm leading-relaxed ${config.destructive ? "bg-red-50 text-red-900" : "bg-blue-50 text-[#0F2C59]"}`}>
            <p>{config.effect}</p>
            <p className="mt-1.5">A pessoa recebe um e-mail avisando, e a mudança fica na trilha de auditoria da conta dela.</p>
          </div>

          {config.days && (
            <div>
              <Label htmlFor="admin-days">Quantos dias a mais</Label>
              <NativeSelect id="admin-days" value={days} onChange={(e) => setDays(e.target.value)} className="mt-1.5"
                aria-describedby="admin-days-hint" data-testid="admin-days">
                {(trialOptions || [7]).map((n) => <option key={n} value={n}>{n} dias</option>)}
              </NativeSelect>
              <p id="admin-days-hint" className="mt-1 text-xs text-slate-600" aria-live="polite">
                Hoje o teste vai até {formatDay(account.subscription?.trial_ends_at)}; passa a ir até {formatDay(newTrialEnd)}.
              </p>
            </div>
          )}

          {config.reason && (
            <div>
              <Label htmlFor="admin-reason">{config.reason.label}{config.reason.required ? " *" : ""}</Label>
              <Textarea id="admin-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={2}
                maxLength={config.reason.max} placeholder={config.reason.placeholder} required={config.reason.required}
                aria-invalid={errors.reason ? true : undefined}
                aria-describedby={errors.reason ? "admin-reason-error" : undefined}
                className={`mt-1.5 ${errors.reason ? "border-red-500 focus-visible:ring-red-500" : ""}`}
                data-testid="admin-reason" />
              {errors.reason && <p id="admin-reason-error" className="mt-1 text-xs text-red-600">{errors.reason}</p>}
            </div>
          )}

          <div>
            <Label htmlFor="admin-confirm">{hasPassword ? "Sua senha *" : "Código do aplicativo autenticador *"}</Label>
            <Input id="admin-confirm" ref={confirmRef} value={confirm} onChange={(e) => setConfirm(e.target.value)}
              type={hasPassword ? "password" : "text"} inputMode={hasPassword ? undefined : "numeric"}
              autoComplete={hasPassword ? "current-password" : "one-time-code"} maxLength={128} required
              aria-invalid={errors.confirm ? true : undefined}
              aria-describedby={["admin-confirm-hint", errors.confirm ? "admin-confirm-error" : null].filter(Boolean).join(" ")}
              className={`mt-1.5 ${errors.confirm ? "border-red-500 focus-visible:ring-red-500" : ""}`}
              data-testid="admin-confirm" />
            <p id="admin-confirm-hint" className="mt-1 text-xs text-slate-600">
              Por segurança, cada ação da administração pede {hasPassword ? "a sua senha" : "o código"} de novo.
            </p>
            {errors.confirm && <p id="admin-confirm-error" className="mt-1 text-xs text-red-600" role="alert">{errors.confirm}</p>}
          </div>

          {errors.form && (
            <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert" data-testid="admin-form-error">
              {errors.form}
            </p>
          )}

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={onClose} disabled={saving}>Cancelar</Button>
            <Button type="submit" disabled={saving} data-testid="admin-submit"
              className={config.destructive ? "bg-red-600 hover:bg-red-700" : "bg-[#1E3A8A] hover:bg-[#0F2C59]"}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{config.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
