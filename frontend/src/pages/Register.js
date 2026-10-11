import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { GoogleLogin } from "@react-oauth/google";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiErrorDetail } from "@/lib/api";
import { LIMITS } from "@/lib/validation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { Loader2, MailCheck } from "lucide-react";
import AuthShell from "@/components/auth/AuthShell";
import { useGoogleButtonWidth } from "@/components/auth/useGoogleButtonWidth";

// ---------------------------------------------------------------------------
// Cadastro de profissional.
//
// SEGURANÇA MÉDIA + backlog (confirmação de e-mail): antes a conta nascia
// aqui mesmo, com a senha digitada. Qualquer pessoa podia criar uma conta com
// o e-mail de outra, e a mensagem "E-mail já cadastrado" revelava quem usa o
// sistema. Agora:
//   1. aqui: nome, e-mail e o aceite da política;
//   2. o servidor manda um link para o e-mail (a resposta é a mesma, exista ou
//      não conta com aquele e-mail);
//   3. no link (/criar-senha) a pessoa cria a senha, e só então a conta nasce.
// Complementares: o cadastro também oferece "Cadastrar com Google", como o login.
// ---------------------------------------------------------------------------
export default function Register() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "" });
  const [terms, setTerms] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sentTo, setSentTo] = useState("");
  // Análise de UI (F1): erro do aceite junto da caixa (antes um aviso que sumia).
  const [termsError, setTermsError] = useState("");
  const [googleBoxRef, googleWidth] = useGoogleButtonWidth();

  const upd = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const requestLink = async () => {
    setLoading(true);
    try {
      const { data } = await api.post("/auth/register", { ...form, terms_accepted: terms });
      setSentTo(data.email);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!terms) {
      setTermsError("Para continuar, marque que leu e aceita os Termos de Uso e a Política de Privacidade.");
      document.getElementById("terms")?.focus();
      return;
    }
    requestLink();
  };

  // Cadastro com Google: o e-mail já vem comprovado pelo Google, sem link. A
  // política é aceita em seguida, na tela de aceite (TermsGate), com evidência.
  const handleGoogle = async (credentialResponse) => {
    setLoading(true);
    try {
      const { data } = await api.post("/auth/google", { credential: credentialResponse.credential });
      setUser(data.user);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Erro no cadastro com o Google");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell headline="Comece a organizar sua clínica hoje."
      tagline="Cadastro rápido e seguro. Seus prontuários protegidos por criptografia e conformidade com o CFP.">
      {sentTo ? (
        <div data-testid="register-check-email">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
            <MailCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <h1 className="mt-4 font-head text-2xl font-bold text-[#0F172A]">Confira seu e-mail</h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-600" role="status">
            Enviamos um link para <b className="text-[#0F172A]">{sentTo}</b>. Abra-o para confirmar o e-mail e criar
            sua senha. O link vale por 24 horas.
          </p>
          <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-slate-500">
            <li>Não chegou? Veja a caixa de spam ou de promoções.</li>
            <li>Se este e-mail já tiver uma conta, a mensagem explica como entrar.</li>
          </ul>
          <div className="mt-6 flex flex-col gap-2">
            <Button type="button" variant="outline" disabled={loading} onClick={requestLink} className="border-slate-300">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Reenviar o link"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setSentTo("")} className="text-[#1E3A8A]">
              Usar outro e-mail
            </Button>
          </div>
        </div>
      ) : (
        <>
          <h1 className="font-head text-2xl font-bold text-[#0F172A]">Criar conta</h1>
          <p className="mt-1 text-sm text-slate-500">Cadastro de profissional</p>
          {/* Produto (assinatura): o teste grátis começa no cadastro, sem cartão. */}
          <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="register-trial-note">
            Comece com o teste grátis, com tudo liberado e sem cartão.
          </p>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <div>
              <Label htmlFor="name" className="text-slate-700">Nome completo</Label>
              {/* Segurança (varredura, item 2): limites iguais aos do servidor. */}
              <Input id="name" required maxLength={LIMITS.professionalName} value={form.name} onChange={upd("name")}
                autoComplete="name" className="mt-1.5" placeholder="Dra. Maria Silva" data-testid="register-name-input" />
            </div>
            <div>
              <Label htmlFor="email" className="text-slate-700">E-mail</Label>
              <Input id="email" type="email" required maxLength={LIMITS.email} value={form.email} onChange={upd("email")}
                autoComplete="email" aria-describedby="email-hint"
                className="mt-1.5" placeholder="voce@clinica.com" data-testid="register-email-input" />
              <p id="email-hint" className="mt-1 text-xs text-slate-500">Você vai receber um link para criar a senha.</p>
            </div>
            <div className="flex items-start gap-2.5 rounded-md bg-[#F0F4F8] p-3">
              <Checkbox id="terms" checked={terms}
                onCheckedChange={(value) => { setTerms(value); if (value) setTermsError(""); }}
                aria-invalid={termsError ? true : undefined} aria-describedby={termsError ? "terms-error" : undefined}
                className="mt-0.5" data-testid="register-terms-checkbox" />
              {/* Conformidade: antes o texto citava "Termos de Privacidade" que não
                  existiam. Agora o link abre a política real, em nova aba para não
                  perder o formulário preenchido. O backend registra o aceite com a
                  versão vigente e a data. */}
              {/* AD1: os Termos de Uso entraram junto (mesma versão, um aceite). */}
              <Label htmlFor="terms" className="text-xs leading-relaxed text-slate-600">
                Li e aceito os{" "}
                <Link to="/termos" target="_blank" rel="noopener noreferrer"
                  className="font-medium text-[#1E3A8A] underline underline-offset-2" data-testid="register-terms-link">
                  Termos de Uso
                </Link>
                {" "}e a{" "}
                <Link to="/privacidade" target="_blank" rel="noopener noreferrer"
                  // Acessibilidade (varredura de UI): link no meio do texto precisa de algo além
                  // da cor (daltonismo, baixa visão): fica sempre sublinhado.
                  className="font-medium text-[#1E3A8A] underline underline-offset-2" data-testid="register-privacy-link">
                  Política de Privacidade
                </Link>
                , que explicam as regras do serviço e como o PsiGestão trata os meus dados e os dos meus pacientes (LGPD).
              </Label>
            </div>
            {/* F1: a mensagem fica aqui, ligada à caixa (aria-describedby), até a pessoa marcar. */}
            {termsError && <p id="terms-error" className="-mt-2 text-xs text-red-600" data-testid="register-terms-error">{termsError}</p>}
            <Button type="submit" disabled={loading}
              className="w-full bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="register-submit-btn">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Continuar"}
            </Button>
          </form>

          <div className="my-5 flex items-center gap-3">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-xs text-slate-500">ou</span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>
          <div ref={googleBoxRef} className="flex w-full justify-center" data-testid="register-google-btn">
            <GoogleLogin onSuccess={handleGoogle} text="signup_with" shape="rectangular" size="large"
              width={String(googleWidth)} onError={() => toast.error("Ocorreu um erro no cadastro com o Google.")} />
          </div>

          <p className="mt-6 text-center text-sm text-slate-500">
            Já tem conta?{" "}
            <Link to="/login" className="font-medium text-[#1E3A8A] hover:underline" data-testid="go-login-link">
              Entrar
            </Link>
          </p>
        </>
      )}
    </AuthShell>
  );
}
