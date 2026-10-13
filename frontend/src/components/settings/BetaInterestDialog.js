import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Sparkles } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PLAN_LABEL, formatBRL, formatDay } from "@/lib/subscription";
import { maskPhone, phoneProblem } from "@/lib/validation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// PRODUTO (assinatura, VERSÃO BETA): o que "Assinar" faz enquanto não há
// cobrança pelo sistema (billing.BETA no servidor).
//
// A pessoa vê o plano e o preço, fica sabendo que é uma versão beta e, se
// quiser, deixa e-mail e telefone para a equipe entrar em contato. Nada é
// cobrado. O e-mail já vem preenchido com o da conta (dá para trocar). Quem já
// deixou o contato vê quando foi e pode atualizar (o servidor guarda um só).
// LGPD: o aviso diz para que serve o dado; a política explica o prazo de guarda.
// ---------------------------------------------------------------------------
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function BetaInterestDialog({ plan, prices, onClose }) {
  const { user } = useAuth();
  const [email, setEmail] = useState(user?.email || "");
  const [phone, setPhone] = useState("");
  const [previous, setPrevious] = useState(null); // contato já deixado (atualizar)
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const emailRef = useRef(null);
  const phoneRef = useRef(null);

  // Já deixou o contato antes? Mostra quando e preenche para atualizar.
  useEffect(() => {
    api.get("/billing/interest").then(({ data }) => {
      if (!data.interest) return;
      setPrevious(data.interest);
      setEmail(data.interest.email);
      setPhone(data.interest.phone);
    }).catch(() => {});
  }, []);

  const price = plan === "annual" ? `${formatBRL(prices.annual)} por ano` : `${formatBRL(prices.monthly)} por mês`;

  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!EMAIL_PATTERN.test(email.trim())) next.email = "E-mail inválido. Ex.: nome@exemplo.com.";
    if (!phone.trim()) next.phone = "Informe o telefone com DDD.";
    else if (phoneProblem(phone)) next.phone = phoneProblem(phone);
    setErrors(next);
    if (next.email) { emailRef.current?.focus(); return; }
    if (next.phone) { phoneRef.current?.focus(); return; }
    setSaving(true);
    try {
      await api.post("/billing/interest", { plan, email: email.trim(), phone: phone.trim() });
      setDone(true);
    } catch (err) {
      setErrors({ form: formatApiErrorDetail(err.response?.data?.detail) || err.message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose(); }}>
      <DialogContent className="sm:max-w-md" data-testid="beta-interest-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-head">
            <Sparkles className="h-5 w-5 text-[#1E3A8A]" aria-hidden="true" /> O PsiGestão está em versão beta
          </DialogTitle>
          <DialogDescription>
            Plano {PLAN_LABEL[plan]}: <b className="font-semibold text-[#0F172A]">{price}</b>
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <div className="space-y-3" role="status" data-testid="beta-interest-done">
            <p className="flex items-start gap-2 text-sm text-emerald-800">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
              Pronto! Recebemos o seu contato. A nossa equipe vai falar com você em breve para combinar a assinatura.
            </p>
            <p className="text-xs text-slate-600">Enviamos uma confirmação para o e-mail da sua conta. Nada foi cobrado.</p>
            <DialogFooter>
              <Button type="button" onClick={onClose} className="bg-[#1E3A8A] hover:bg-[#0F2C59]">Fechar</Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={submit} noValidate className="space-y-4">
            <p className="text-sm leading-relaxed text-slate-700">
              A assinatura ainda não é feita pelo sistema e nada é cobrado por enquanto. Se tiver interesse, deixe o seu
              e-mail e telefone: a nossa equipe entra em contato para combinar.
            </p>
            {previous && (
              <p className="rounded-md bg-blue-50 px-3 py-2 text-xs text-[#0F2C59]" data-testid="beta-interest-previous">
                Você já deixou o seu contato em {formatDay(previous.updated_at || previous.created_at)} (plano{" "}
                {PLAN_LABEL[previous.plan]}). Enviar de novo atualiza os dados.
              </p>
            )}
            <div>
              <Label htmlFor="beta-email">E-mail para contato *</Label>
              <Input id="beta-email" ref={emailRef} type="email" inputMode="email" autoComplete="email" required
                maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)}
                aria-invalid={errors.email ? true : undefined}
                aria-describedby={errors.email ? "beta-email-error" : undefined}
                className={`mt-1.5 ${errors.email ? "border-red-500 focus-visible:ring-red-500" : ""}`}
                data-testid="beta-email" />
              {errors.email && <p id="beta-email-error" className="mt-1 text-xs text-red-600">{errors.email}</p>}
            </div>
            <div>
              <Label htmlFor="beta-phone">Telefone (com DDD) *</Label>
              <Input id="beta-phone" ref={phoneRef} type="tel" inputMode="tel" autoComplete="tel" required
                maxLength={30} value={phone} onChange={(e) => setPhone(maskPhone(e.target.value))}
                placeholder="(11) 91234-5678"
                aria-invalid={errors.phone ? true : undefined}
                aria-describedby={errors.phone ? "beta-phone-error" : undefined}
                className={`mt-1.5 ${errors.phone ? "border-red-500 focus-visible:ring-red-500" : ""}`}
                data-testid="beta-phone" />
              {errors.phone && <p id="beta-phone-error" className="mt-1 text-xs text-red-600">{errors.phone}</p>}
            </div>
            <p className="text-xs leading-relaxed text-slate-600">
              Usamos esses dados só para falar com você sobre a assinatura. Saiba mais na{" "}
              <a href="/privacidade" target="_blank" rel="noopener noreferrer" className="font-medium text-[#1E3A8A] underline underline-offset-2">
                Política de Privacidade (abre em nova aba)
              </a>.
            </p>
            {errors.form && (
              <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">{errors.form}</p>
            )}
            <DialogFooter className="gap-2 sm:gap-0">
              <Button type="button" variant="outline" onClick={onClose} disabled={saving}>Agora não</Button>
              <Button type="submit" disabled={saving} className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="beta-submit">
                {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Quero ser contatada(o)
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
