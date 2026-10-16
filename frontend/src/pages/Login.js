import { useState } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiErrorDetail } from "@/lib/api";
import { LIMITS } from "@/lib/validation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { Loader2, MailCheck, ShieldCheck } from "lucide-react";
// Importar o componente de Login do Google (necessita de 'npm install @react-oauth/google')
import { GoogleLogin } from "@react-oauth/google";
import AuthCard from "@/components/auth/AuthCard";
import PasswordInput from "@/components/auth/PasswordInput";
import { useGoogleButtonWidth } from "@/components/auth/useGoogleButtonWidth";

// ---------------------------------------------------------------------------
// DESIGN (pedido do usuário, referência visual anexada): login e cadastro
// passaram a dividir o MESMO cartão (AuthCard), alternando com um botão no
// painel decorativo, em vez de duas páginas/rotas separadas. Register.js saiu
// (a lógica de cadastro mora aqui agora); App.js aponta /login E /register
// para este componente — o caminho só decide o modo INICIAL (abaixo), a
// troca pelo cartão é sempre estado local (sem navegação), para a animação
// de deslizar não piscar remontando o componente.
//
// Tudo que já existia continua: login por e-mail/senha, verificação em duas
// etapas (código do app ou recuperação), login/cadastro com Google, "Esqueci
// minha senha", aceite dos Termos no cadastro (com erro ligado à caixa, F1),
// cadastro em dois passos por e-mail ("confira seu e-mail"), e os erros fixos
// no formulário em vez de toast (acessibilidade, item A1 da varredura).
// ---------------------------------------------------------------------------

const HERO_COPY = {
  // Mostrado enquanto o modo ATUAL é "login": convite para quem ainda não tem conta.
  login: {
    title: "Ainda não tem conta?",
    text: "Organize suas consultas com prontuários protegidos por criptografia e conformidade com o CFP.",
    buttonLabel: "Criar conta",
  },
  // Mostrado enquanto o modo ATUAL é "register": convite para quem já tem conta.
  register: {
    title: "Já tem conta?",
    text: "Entre para continuar cuidando dos seus pacientes com segurança.",
    buttonLabel: "Entrar",
  },
};

export default function Login() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // PRODUTO: depois de entrar, volta para a página onde a sessão expirou
  // (ex.: a ficha do paciente com uma anotação em rascunho), se houver.
  const from = location.state?.from;
  const afterLogin = from ? `${from.pathname}${from.search || ""}` : "/dashboard";

  // Modo inicial pelo caminho (/login ou /register); depois disso, só o
  // botão do cartão troca (estado local, sem navegar — ver comentário acima).
  const [mode, setMode] = useState(() => (location.pathname === "/register" ? "register" : "login"));
  const switchMode = () => setMode((current) => (current === "login" ? "register" : "login"));

  // ---- login (e-mail/senha + duas etapas) ----
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  // Segurança (backlog: MFA): senha certa numa conta com verificação em duas
  // etapas -> o servidor devolve um mfa_token e pede o código do aplicativo.
  const [mfaToken, setMfaToken] = useState(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  // Acessibilidade (varredura, item A1): e-mail/senha ou código incorretos
  // ficavam só num toast, que desaparece em até ~10s e não tem relação com
  // nenhum campo — quem usa leitor de tela e não estava "ouvindo" no momento
  // perdia a explicação. Agora o erro fica num bloco fixo (role="alert"),
  // como nos outros formulários do sistema. Não marca um campo específico
  // (e-mail OU senha): a mensagem já é genérica de propósito, para não revelar
  // qual dos dois está errado.
  const [loginError, setLoginError] = useState("");
  const [mfaError, setMfaError] = useState("");

  // ---- cadastro (nome/e-mail + aceite + link por e-mail) ----
  const [form, setForm] = useState({ name: "", email: "" });
  const [terms, setTerms] = useState(false);
  const [registerLoading, setRegisterLoading] = useState(false);
  const [sentTo, setSentTo] = useState("");
  // Análise de UI (F1): erro do aceite junto da caixa (antes um aviso que sumia).
  const [termsError, setTermsError] = useState("");

  // Um botão do Google por vez (só o do modo atual está montado) — callback
  // ref (ver o hook): mede de novo a cada troca de modo, não só uma vez.
  const [googleBoxRef, googleWidth] = useGoogleButtonWidth();

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
  const handleGoogleLogin = async (credentialResponse) => {
    setLoading(true);
    try {
      // Envia o token JWT (credential) para a API
      const { data } = await api.post("/auth/google", { credential: credentialResponse.credential });
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

  // ---- cadastro: handlers (portados de Register.js) ----
  const updateForm = (key) => (e) => setForm((current) => ({ ...current, [key]: e.target.value }));

  const requestLink = async () => {
    setRegisterLoading(true);
    try {
      const { data } = await api.post("/auth/register", { ...form, terms_accepted: terms });
      setSentTo(data.email);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setRegisterLoading(false);
    }
  };

  const handleRegisterSubmit = (e) => {
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
  const handleGoogleRegister = async (credentialResponse) => {
    setRegisterLoading(true);
    try {
      const { data } = await api.post("/auth/google", { credential: credentialResponse.credential });
      setUser(data.user);
      navigate(afterLogin, { replace: true });
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Erro no cadastro com o Google");
    } finally {
      setRegisterLoading(false);
    }
  };

  const hero = HERO_COPY[mode];

  // ---------------------------------------------------------------- título
  let formTitle;
  let formSubtitle;
  if (mode === "login") {
    if (mfaToken) {
      formTitle = "Verificação em duas etapas";
      formSubtitle = useRecovery
        ? "Digite um dos códigos de recuperação que você guardou ao ativar a verificação. Cada um vale uma vez."
        : "Abra o aplicativo autenticador no celular e digite o código de 6 dígitos do PsiGestão.";
    } else {
      formTitle = "Entrar";
      formSubtitle = "Acesse o painel do profissional";
    }
  } else if (sentTo) {
    formTitle = "Confira seu e-mail";
  } else {
    formTitle = "Criar conta";
    formSubtitle = "Cadastro de profissional";
  }

  return (
    <AuthCard mode={mode} hero={hero} onSwitch={switchMode} formTitle={formTitle} formSubtitle={formSubtitle}>
      {mode === "login" ? (
        mfaToken ? (
          // ------------------------------------------------ login: passo 2 (MFA)
          <>
            <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            {/* psi-stagger (prévia validada): cada campo aparece um instante
                depois do anterior, em vez de tudo de uma vez. */}
            <form onSubmit={handleMfa} className="psi-stagger space-y-4">
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
              {/* transition-all + active:scale (prévia validada): resposta ao
                  toque no clique, sombra mais forte no hover. */}
              <Button type="submit" disabled={loading}
                className="w-full bg-[#1E3A8A] transition-all hover:bg-[#0F2C59] hover:shadow-lg hover:shadow-[#1E3A8A]/30 active:scale-[0.98]"
                data-testid="login-mfa-submit">
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
          // ------------------------------------------------ login: passo 1
          <>
            {/* psi-stagger (prévia validada): cada campo aparece um instante
                depois do anterior, em vez de tudo de uma vez. */}
            <form onSubmit={handleEmailLogin} className="psi-stagger space-y-4">
              <div>
                <Label htmlFor="email" className="text-slate-700">E-mail</Label>
                {/* Segurança (varredura, item 2): limites iguais aos do servidor. */}
                <Input
                  id="email" type="email" required maxLength={LIMITS.email} value={email} autoComplete="username"
                  onChange={(e) => { setEmail(e.target.value); setLoginError(""); }}
                  placeholder="voce@clinica.com" className="mt-1.5"
                  data-testid="login-email-input"
                />
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
              {/* transition-all + active:scale (prévia validada): resposta ao
                  toque no clique, sombra mais forte no hover. */}
              <Button
                type="submit" disabled={loading}
                className="w-full bg-[#1E3A8A] transition-all hover:bg-[#0F2C59] hover:shadow-lg hover:shadow-[#1E3A8A]/30 active:scale-[0.98]"
                data-testid="login-submit-btn"
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Entrar"}
              </Button>
            </form>

            <div className="my-5 flex items-center gap-3">
              <div className="h-px flex-1 bg-slate-200" />
              <span className="text-xs text-slate-500">ou</span>
              <div className="h-px flex-1 bg-slate-200" />
            </div>

            <div ref={googleBoxRef} className="flex w-full justify-center" data-testid="login-google-btn">
              <GoogleLogin
                onSuccess={handleGoogleLogin}
                onError={() => toast.error("Ocorreu um erro no processo de login com o Google.")}
                shape="rectangular"
                size="large"
                width={String(googleWidth)}
              />
            </div>

            <p className="mt-6 text-center text-sm text-slate-500">
              Não tem conta?{" "}
              <button type="button" onClick={switchMode}
                className="rounded font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A] focus-visible:ring-offset-2"
                data-testid="go-register-link">
                Cadastre-se
              </button>
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
        )
      ) : sentTo ? (
        // ------------------------------------------------ cadastro: confira o e-mail
        <div data-testid="register-check-email">
          <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
            <MailCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <p className="text-sm leading-relaxed text-slate-600" role="status">
            Enviamos um link para <b className="text-[#0F172A]">{sentTo}</b>. Abra-o para confirmar o e-mail e criar
            sua senha. O link vale por 24 horas.
          </p>
          <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-slate-500">
            <li>Não chegou? Veja a caixa de spam ou de promoções.</li>
            <li>Se este e-mail já tiver uma conta, a mensagem explica como entrar.</li>
          </ul>
          <div className="mt-6 flex flex-col gap-2">
            <Button type="button" variant="outline" disabled={registerLoading} onClick={requestLink} className="border-slate-300">
              {registerLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Reenviar o link"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setSentTo("")} className="text-[#1E3A8A]">
              Usar outro e-mail
            </Button>
          </div>
        </div>
      ) : (
        // ------------------------------------------------ cadastro: passo 1
        <>
          {/* Produto (assinatura): o teste grátis começa no cadastro, sem cartão.
              Compacto (prévia validada pelo usuário): texto mais curto (cabe
              numa linha) e padding menor — o cartão é mais estreito que uma
              página inteira, então cada px de respiro conta. */}
          <p className="mb-2.5 rounded-md bg-emerald-50 px-3 py-1.5 text-xs text-emerald-800" data-testid="register-trial-note">
            Teste grátis, tudo liberado, sem cartão.
          </p>

          {/* psi-stagger (prévia validada): cada campo aparece um instante
              depois do anterior, em vez de tudo de uma vez. */}
          <form onSubmit={handleRegisterSubmit} className="psi-stagger space-y-2.5">
            <div>
              <Label htmlFor="name" className="text-slate-700">Nome completo</Label>
              {/* Segurança (varredura, item 2): limites iguais aos do servidor. */}
              <Input id="name" required maxLength={LIMITS.professionalName} value={form.name} onChange={updateForm("name")}
                autoComplete="name" className="mt-1.5" placeholder="Dra. Maria Silva" data-testid="register-name-input" />
            </div>
            <div>
              <Label htmlFor="register-email" className="text-slate-700">E-mail</Label>
              <Input id="register-email" type="email" required maxLength={LIMITS.email} value={form.email} onChange={updateForm("email")}
                autoComplete="email" aria-describedby="email-hint"
                className="mt-1.5" placeholder="voce@clinica.com" data-testid="register-email-input" />
              <p id="email-hint" className="mt-1 text-xs text-slate-500">Você vai receber um link para criar a senha.</p>
            </div>
            <div className="flex items-start gap-2.5 rounded-md bg-[#F0F4F8] p-2">
              <Checkbox id="terms" checked={terms}
                onCheckedChange={(value) => { setTerms(value); if (value) setTermsError(""); }}
                aria-invalid={termsError ? true : undefined} aria-describedby={termsError ? "terms-error" : undefined}
                className="mt-0.5" data-testid="register-terms-checkbox" />
              {/* Conformidade: o texto cita os documentos reais, abertos em nova aba
                  para não perder o formulário preenchido. O backend registra o
                  aceite com a versão vigente e a data (AD1: um aceite só). */}
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
            {/* transition-all + active:scale (prévia validada): resposta ao
                toque no clique, sombra mais forte no hover. */}
            <Button type="submit" disabled={registerLoading}
              className="w-full bg-[#1E3A8A] transition-all hover:bg-[#0F2C59] hover:shadow-lg hover:shadow-[#1E3A8A]/30 active:scale-[0.98]"
              data-testid="register-submit-btn">
              {registerLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Continuar"}
            </Button>
          </form>

          <div className="my-3 flex items-center gap-3">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-xs text-slate-500">ou</span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>
          <div ref={googleBoxRef} className="flex w-full justify-center" data-testid="register-google-btn">
            <GoogleLogin onSuccess={handleGoogleRegister} text="signup_with" shape="rectangular" size="large"
              width={String(googleWidth)} onError={() => toast.error("Ocorreu um erro no cadastro com o Google.")} />
          </div>

          <p className="mt-3 text-center text-sm text-slate-500">
            Já tem conta?{" "}
            <button type="button" onClick={switchMode}
              className="rounded font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A] focus-visible:ring-offset-2"
              data-testid="go-login-link">
              Entrar
            </button>
          </p>
        </>
      )}
    </AuthCard>
  );
}
