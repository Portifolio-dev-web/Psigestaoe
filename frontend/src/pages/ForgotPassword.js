import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, Loader2, MailCheck } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { LIMITS } from "@/lib/validation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import AuthShell from "@/components/auth/AuthShell";

// ---------------------------------------------------------------------------
// "Esqueci minha senha" (segurança média). Antes não existia: quem esquecia a
// senha perdia o acesso aos prontuários. O servidor manda um link de uso
// único (vale 1 hora) e responde SEMPRE a mesma coisa, exista ou não conta
// com o e-mail, para não revelar quem usa o sistema.
// ---------------------------------------------------------------------------
export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await api.post("/auth/password/forgot", { email });
      setSent(true);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell headline="Recupere o acesso com segurança."
      tagline="O link de troca de senha vai só para o e-mail da conta e vale uma única vez.">
      {sent ? (
        <div data-testid="forgot-sent">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
            <MailCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <h1 className="mt-4 font-head text-2xl font-bold text-[#0F172A]">Confira seu e-mail</h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-600" role="status">
            Se houver uma conta com <b className="text-[#0F172A]">{email}</b>, enviamos um link para criar uma nova
            senha. Ele vale por 1 hora e só pode ser usado uma vez.
          </p>
          <p className="mt-3 text-sm text-slate-500">Não chegou? Veja a caixa de spam ou peça de novo em alguns minutos.</p>
          <Button type="button" variant="outline" onClick={() => setSent(false)} className="mt-6 w-full border-slate-300">
            Pedir de novo
          </Button>
        </div>
      ) : (
        <>
          <h1 className="font-head text-2xl font-bold text-[#0F172A]">Esqueci minha senha</h1>
          <p className="mt-1 text-sm text-slate-500">Informe o e-mail da conta e enviaremos um link para criar uma nova senha.</p>
          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <Label htmlFor="forgot-email" className="text-slate-700">E-mail</Label>
              <Input id="forgot-email" type="email" required autoFocus maxLength={LIMITS.email} autoComplete="email"
                value={email} onChange={(e) => setEmail(e.target.value)} placeholder="voce@clinica.com"
                className="mt-1.5" data-testid="forgot-email-input" />
            </div>
            <Button type="submit" disabled={loading} className="w-full bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="forgot-submit">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Enviar link"}
            </Button>
          </form>
        </>
      )}
      <Link to="/login" className="mt-6 flex items-center justify-center gap-1.5 text-sm text-slate-500 hover:text-[#1E3A8A]">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Voltar para o login
      </Link>
    </AuthShell>
  );
}
