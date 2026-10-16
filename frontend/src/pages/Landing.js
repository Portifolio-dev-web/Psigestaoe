import { Link } from "react-router-dom";
import { Stethoscope } from "lucide-react";

// ---------------------------------------------------------------------------
// DESIGN (pedido do usuário, prévia validada): tela de entrada em "/", antes
// do login/cadastro. Antes não existia rota "/" — quem abria o site direto
// caía na página 404 (item D2 da varredura). Fica de FORA do AppBranch
// (App.js), igual a /login e /register: PublicOnly manda quem já tem sessão
// ativa direto para o Painel.
//
// Fundo em degrade ESTÁTICO (não o GradientWave/WebGL do cartão de login) —
// de propósito: é a primeira coisa que carrega, antes de qualquer script
// pesado, então fica leve por padrão. O cartão de verdade (com a animação)
// só entra quando a pessoa já escolheu "Entrar" ou "Criar conta".
// ---------------------------------------------------------------------------
export default function Landing() {
  return (
    // <main> (não <div>): o RouteAnnouncer (App.js) procura essa região para
    // mover o foco e anunciar a tela ao navegar — mesma regra de toda tela
    // pública (ver PublicPageShell.js). tabIndex -1: recebe foco por código,
    // sem entrar na ordem do Tab.
    <main tabIndex={-1} className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-gradient-to-br from-[#0F2C59] via-[#1E3A8A] to-[#0369A1] px-4 py-10 text-center text-white focus:outline-none">
      <div className="pointer-events-none absolute inset-0" aria-hidden="true" style={{
        background: "radial-gradient(900px circle at 20% 15%, rgba(255,255,255,0.08), transparent 55%),"
          + "radial-gradient(700px circle at 80% 85%, rgba(255,255,255,0.06), transparent 55%)",
      }} />

      {/* psi-fade-up: entrada leve (fade + subida), desliga sozinha com
          "reduzir movimento" (mesma classe usada no cartão de login). */}
      <div className="psi-fade-up relative z-10 flex max-w-md flex-col items-center gap-5">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white/15">
          <Stethoscope className="h-7 w-7" aria-hidden="true" />
        </div>
        <span className="font-head text-xl font-bold">PsiGestão</span>
        <div>
          <h1 className="font-head text-3xl font-extrabold leading-tight sm:text-4xl">
            Cuidado clínico com segurança de dados.
          </h1>
          <p className="mx-auto mt-3 max-w-sm text-sm leading-relaxed text-blue-100">
            Prontuários criptografados, trilha de auditoria e conformidade LGPD para profissionais de psicologia.
          </p>
        </div>
        <div className="mt-1 flex w-full max-w-xs flex-col gap-3 sm:max-w-none sm:flex-row">
          <Link to="/login"
            className="flex-1 rounded-lg bg-white px-6 py-3 text-sm font-semibold text-[#0F2C59] transition-all hover:shadow-lg hover:shadow-black/30 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0F2C59]"
            data-testid="landing-login-link">
            Entrar
          </Link>
          <Link to="/register"
            className="flex-1 rounded-lg border border-white/30 bg-white/10 px-6 py-3 text-sm font-semibold text-white backdrop-blur-sm transition-all hover:bg-white/20 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0F2C59]"
            data-testid="landing-register-link">
            Criar conta
          </Link>
        </div>
      </div>

      <p className="psi-fade-up relative z-10 mt-10 text-xs text-blue-200" style={{ animationDelay: "0.12s" }}>
        Dados criptografados · HTTPS/TLS · CFP
      </p>
      <p className="psi-fade-up relative z-10 mt-2 text-xs text-blue-300" style={{ animationDelay: "0.18s" }}>
        <Link to="/termos" className="underline underline-offset-2 hover:text-white">Termos de Uso</Link>
        <span className="mx-2" aria-hidden="true">·</span>
        <Link to="/privacidade" className="underline underline-offset-2 hover:text-white">Política de Privacidade</Link>
      </p>
    </main>
  );
}
