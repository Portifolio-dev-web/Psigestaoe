import { useState } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiErrorDetail } from "@/lib/api";
import { LIMITS } from "@/lib/validation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Loader2, Mail, ShieldCheck } from "lucide-react";
// Importar o componente de Login do Google (necessita de 'npm install @react-oauth/google')
import { GoogleLogin } from "@react-oauth/google";
import AuthShell from "@/components/auth/AuthShell";
import PasswordInput from "@/components/auth/PasswordInput";
import { useGoogleButtonWidth } from "@/components/auth/useGoogleButtonWidth";

// O fundo animado do painel esquerdo (antes uma foto do Unsplash) e o layout
// ficam em components/auth/AuthShell.js, compartilhados pelas telas de entrada.

export default function Login() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // PRODUTO: depois de entrar, volta para a página onde a sessão expirou
  // (ex.: a ficha do paciente com uma anotação em rascunho), se houver.
  const from = location.state?.from;
  const afterLogin = from ? `${from.pathname}${from.search || ""}` : "/dashboard";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  // Segurança (backlog: MFA): senha certa numa conta com verificação em duas
  // etapas -> o servidor devolve um mfa_token e pede o código do aplicativo.
  const [mfaToken, setMfaToken] = useState(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [googleBoxRef, googleWidth] = useGoogleButtonWidth();
  // Acessibilidade (varredura, item A1): e-mail/senha ou código incorretos
  // ficavam só num toast, que desaparece em até ~10s e não tem relação com
  // nenhum campo — quem usa leitor de tela e não estava "ouvindo" no momento
  // perdia a explicação. Agora o erro fica num bloco fixo (role="alert"),
  // como nos outros formulários do sistema. Não marca um campo específico
  // (e-mail OU senha): a mensagem já é genérica de propósito, para não revelar
  // qual dos dois está errado.
  const [loginError, setLoginError] = useState("");
  const [mfaError, setMfaError] = useState("");

  const finishLogin = (user) => {
    setUser(user);
    toast.success("Bem-vindo(a) de volta!");
    navigate(afterLogin, { replace: true });
  };

  const handleEmailLogin = async (e) => {
    e.preventDefault();
    setLoginError("");
    setLoading(true);
    try {
      const { data } = await api.post("/auth/login", { email, password });
      if (data.mfa_required) {
        setMfaToken(data.mfa_token);
        setCode("");
        setUseRecovery(false);
        return;
      }
      finishLogin(data.user);
    } catch (err) {
      // A1: erro fixo no formulário (abaixo do botão), não um toast que some.
      setLoginError(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      requestAnimationFrame(() => document.getElementById("login-form-error")?.focus());
    } finally {
      setLoading(false);
    }
  };

  const handleMfa = async (e) => {
    e.preventDefault();
    setMfaError("");
    setLoading(true);
    try {
      const { data } = await api.post("/auth/login/mfa", { mfa_token: mfaToken, code: code.trim() });
      finishLogin(data.user);
    } catch (err) {
      const detail = err.response?.data?.detail;
      // O passo 2 vale 5 minutos: passou disso, volta para a senha. Aqui o toast
      // cabe (A1 trata do erro DENTRO do passo 2): a tela muda para o passo 1,
      // então um erro fixo morreria com ela — é um aviso de transição, não a
      // correção de um campo que continua na tela.
      if (detail?.code === "mfa_expired") {
        setMfaToken(null);
        setPassword("");
        toast.error(formatApiErrorDetail(detail) || err.message);
        return;
      }
      // A1: idem, erro fixo ligado ao campo do código (aria-describedby abaixo).
      setMfaError(formatApiErrorDetail(detail) || err.message);
      setCode("");
      requestAnimationFrame(() => document.getElementById("mfa-code")?.focus());
    } finally {
      setLoading(false);
    }
  };

  // Nova função para comunicar com a rota /auth/google do backend
  const handleGoogleSuccess = async (credentialResponse) => {
    setLoading(true);
    try {
      // Envia o token JWT (credential) para a API
      const { data } = await api.post("/auth/google", {
        credential: credentialResponse.credential
      });
      setUser(data.user);
      if (data.password_discarded) {
        // SEGURANÇA (varredura, item 5): esta conta tinha uma senha criada sem
        // confirmar o e-mail (pode ter sido outra pessoa). O Google acabou de
        // comprovar quem é o dono, então o servidor desativou a senha antiga e
        // encerrou as outras sessões. Explicamos, para não parecer um defeito.
        toast.warning(
          "Por segurança, a senha antiga desta conta foi desativada: ela tinha sido criada sem confirmar que o e-mail era seu. "
          // UX: o menu "Privacidade & LGPD" passou a se chamar "Configurações".
          + "Continue entrando com o Google ou defina uma nova senha em Configurações > Minha conta.",
          { duration: 12000 },
        );
      } else {
        toast.success("Autenticação com Google efetuada com sucesso!");
      }
      navigate(afterLogin, { replace: true });
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Erro na autenticação do Google");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell headline="Cuidado clínico com segurança de dados."
      tagline="Prontuários criptografados, trilha de auditoria e conformidade LGPD para profissionais de psicologia.">
      {mfaToken ? (
        // ------------------------------------------------ passo 2 (MFA)
        <>
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <h1 className="mt-4 font-head text-2xl font-bold text-[#0F172A]">Verificação em duas etapas</h1>
          <p className="mt-1 text-sm text-slate-500">
            {useRecovery
              ? "Digite um dos códigos de recuperação que você guardou ao ativar a verificação. Cada um vale uma vez."
              : "Abra o aplicativo autenticador no celular e digite o código de 6 dígitos do PsiGestão."}
          </p>
          <form onSubmit={handleMfa} className="mt-6 space-y-4">
            <div>
              <Label htmlFor="mfa-code" className="text-slate-700">{useRecovery ? "Código de recuperação" : "Código do aplicativo"}</Label>
              {/* autoComplete="one-time-code": o celular oferece o código para colar. */}
              <Input id="mfa-code" value={code} onChange={(e) => { setCode(e.target.value); setMfaError(""); }}
                required autoFocus autoComplete="one-time-code" maxLength={useRecovery ? 32 : 6}
                inputMode={useRecovery ? "text" : "numeric"} pattern={useRecovery ? undefined : "[0-9]{6}"}
                placeholder={useRecovery ? "xxxx-xxxx-xxxx-xxxx" : "000000"}
                aria-invalid={mfaError ? true : undefined}
                aria-describedby={mfaError ? "mfa-code-error" : undefined}
                className={`mt-1.5 text-center font-mono text-lg tracking-[0.3em] ${mfaError ? "border-red-500 focus-visible:ring-red-500" : ""}`}
                data-testid="login-mfa-input" />
              {/* A1: erro do código fica ligado ao campo (não some sozinho como o toast antigo). */}
              {mfaError && <p id="mfa-code-error" role="alert" className="mt-1.5 text-sm text-red-600" data-testid="login-mfa-error">{mfaError}</p>}
            </div>
            <Button type="submit" disabled={loading} className="w-full bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="login-mfa-submit">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirmar"}
            </Button>
          </form>
          <div className="mt-5 flex flex-col items-center gap-2 text-sm">
            <button type="button" className="font-medium text-[#1E3A8A] hover:underline"
              onClick={() => { setUseRecovery((value) => !value); setCode(""); setMfaError(""); }}>
              {useRecovery ? "Usar o código do aplicativo" : "Perdi o celular: usar um código de recuperação"}
            </button>
            <button type="button" className="text-slate-500 hover:text-[#1E3A8A] hover:underline"
              onClick={() => { setMfaToken(null); setPassword(""); }}>
              Voltar
            </button>
          </div>
        </>
      ) : (
        // ------------------------------------------------ passo 1
        <>
          <h1 className="font-head text-2xl font-bold text-[#0F172A]">Entrar</h1>
          <p className="mt-1 text-sm text-slate-500">Acesse o painel do profissional</p>

          <form onSubmit={handleEmailLogin} className="mt-6 space-y-4">
            <div>
              <Label htmlFor="email" className="text-slate-700">E-mail</Label>
              <div className="relative mt-1.5">
                <Mail className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
                {/* Segurança (varredura, item 2): limites iguais aos do servidor. */}
                <Input
                  id="email" type="email" required maxLength={LIMITS.email} value={email} autoComplete="username"
                  onChange={(e) => { setEmail(e.target.value); setLoginError(""); }}
                  placeholder="voce@clinica.com" className="pl-9"
                  data-testid="login-email-input"
                />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between">
                <Label htmlFor="password" className="text-slate-700">Senha</Label>
                {/* Segurança média: antes não havia como recuperar a senha esquecida. */}
                <Link to="/esqueci-senha" className="text-xs font-medium text-[#1E3A8A] hover:underline" data-testid="forgot-password-link">
                  Esqueci minha senha
                </Link>
              </div>
              {/* Complementares (login): botão de mostrar a senha. E sem o
                  "••••••••" de exemplo, que parecia uma senha já preenchida. */}
              <div className="mt-1.5">
                <PasswordInput
                  id="password" required withIcon maxLength={LIMITS.loginPassword} value={password}
                  autoComplete="current-password" onChange={(e) => { setPassword(e.target.value); setLoginError(""); }}
                  aria-invalid={loginError ? true : undefined}
                  aria-describedby={loginError ? "login-form-error" : undefined}
                  data-testid="login-password-input"
                />
              </div>
            </div>
            {/* A1: erro fixo (role="alert"), não um toast que desaparece em ~10s.
                Não marca e-mail OU senha especificamente: a mensagem já é genérica
                de propósito (não revela qual dos dois está errado). */}
            {loginError && (
              <p id="login-form-error" role="alert" tabIndex={-1}
                className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
                data-testid="login-form-error">
                {loginError}
              </p>
            )}
            <Button
              type="submit" disabled={loading}
              className="w-full bg-[#1E3A8A] hover:bg-[#0F2C59]"
              data-testid="login-submit-btn"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Entrar"}
            </Button>
          </form>

          <div className="my-5 flex items-center gap-3">
            <div className="h-px flex-1 bg-slate-200" />
            {/* Acessibilidade (varredura de UI): slate-400 -> slate-500 (contraste mínimo 4,5:1). */}
            <span className="text-xs text-slate-500">ou</span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>

          {/* Substitui o botão original pelo componente oficial da Google */}
          <div ref={googleBoxRef} className="flex w-full justify-center" data-testid="login-google-btn">
            <GoogleLogin
              onSuccess={handleGoogleSuccess}
              onError={() => toast.error("Ocorreu um erro no processo de login com o Google.")}
              shape="rectangular"
              size="large"
              width={String(googleWidth)}
            />
          </div>

          <p className="mt-6 text-center text-sm text-slate-500">
            Não tem conta?{" "}
            <Link to="/register" className="font-medium text-[#1E3A8A] hover:underline" data-testid="go-register-link">
              Cadastre-se
            </Link>
          </p>
          {/* Só um link de acesso. NÃO usar "ao entrar, você concorda...": aceite
              implícito não vale como evidência. O aceite explícito acontece no
              cadastro ou na tela de aceite (TermsGate). */}
          <p className="mt-3 text-center text-xs">
            {/* AD1: os Termos de Uso também ficam a um clique. */}
            <Link to="/termos" className="text-slate-500 hover:text-[#1E3A8A] hover:underline" data-testid="login-terms-link">
              Termos de Uso
            </Link>
            <span className="mx-2 text-slate-300" aria-hidden="true">·</span>
            <Link to="/privacidade" className="text-slate-500 hover:text-[#1E3A8A] hover:underline" data-testid="login-privacy-link">
              Política de Privacidade
            </Link>
          </p>
        </>
      )}
    </AuthShell>
  );
}
