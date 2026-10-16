import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { LIMITS } from "@/lib/validation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import AuthCard from "@/components/auth/AuthCard";

// ---------------------------------------------------------------------------
// "Esqueci minha senha" (segurança média). Antes não existia: quem esquecia a
// senha perdia o acesso aos prontuários. O servidor manda um link de uso
// único (vale 1 hora) e responde SEMPRE a mesma coisa, exista ou não conta
// com o e-mail, para não revelar quem usa o sistema.
//
// DESIGN (mesmo visual de login/cadastro): reaproveita o AuthCard, mas sem
// alternar de modo — não existe "outro lado" aqui como em entrar/cadastrar,
// então `mode` fica fixo em "login" (o painel decorativo sempre à direita,
// parado) e o botão dele volta para /login de verdade (navegação, não
// estado local — esta é outra página/rota, diferente do cartão de Login.js).
// ---------------------------------------------------------------------------
const HERO = {
  title: "Lembrou da senha?",
  text: "Volte para a tela de entrada e acesse sua conta normalmente.",
  buttonLabel: "Entrar",
};

export default function ForgotPassword() {
  const navigate = useNavigate();
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
    <AuthCard mode="login" hero={HERO} onSwitch={() => navigate("/login")}
      formTitle={sent ? "Confira seu e-mail" : "Esqueci minha senha"}
      formSubtitle={sent ? undefined : "Informe o e-mail da conta e enviaremos um link para criar uma nova senha."}>
      {sent ? (
        <div data-testid="forgot-sent">
          <p className="text-sm leading-relaxed text-slate-600" role="status">
            Se houver uma conta com <b className="text-[#0F172A]">{email}</b>, enviamos um link para criar uma nova
            senha. Ele vale por 1 hora e só pode ser usado uma vez.
          </p>
          <p className="mt-3 text-sm text-slate-500">Não chegou? Veja a caixa de spam ou peça de novo em alguns minutos.</p>
          <Button type="button" variant="outline" onClick={() => setSent(false)} className="mt-6 w-full border-slate-300">
            Pedir de novo
          </Button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
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
      )}
      <p className="mt-6 text-center text-sm text-slate-500">
        <Link to="/login" className="font-medium text-[#1E3A8A] hover:underline">
          Voltar para o login
        </Link>
      </p>
    </AuthCard>
  );
}
