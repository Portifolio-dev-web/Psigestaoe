import { useEffect, useRef } from "react";
import { Stethoscope } from "lucide-react";
import { GradientWave } from "@/components/ui/gradient-wave";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// DESIGN (pedido do usuário, referência visual anexada): cartão com dois
// painéis que trocam de lado ao alternar entre entrar/cadastrar — um painel
// decorativo (fundo animado + convite para o outro modo) e o painel do
// formulário. Substitui o AuthShell (split tela inteira) só nas telas de
// login/cadastro, que agora dividem o MESMO componente (ver pages/Login.js).
//
// Diferenças da referência (decisões deste projeto, não da imagem):
//   - um H1/texto decorativo só (não dois elementos animando em paralelo):
//     o painel colorido inteiro desliza como um bloco só, o texto vem junto.
//     Mesmo efeito visual, menos elementos para sincronizar e menos chance
//     de um ficar com aria-hidden errado.
//   - altura por min-height (não fixa): a ficha de cadastro tem mais campos
//     que a de login; um valor fixo (como o 520px da referência) cortaria
//     conteúdo ou sobraria vazio dependendo do modo.
//   - no celular (abaixo de "lg"), nada desliza: os painéis empilham (o
//     decorativo vira uma faixa no topo) e o conteúdo troca com um fade
//     (.psi-fade-up, já usada no resto do app) — deslizar um painel de 50%
//     de largura não faz sentido numa tela estreita, e qualquer animação
//     aqui seria só decorativa, sem ganho de clareza (WCAG 1.4.10, reflow).
//   - "reduzir movimento": a troca de lado também usa motion-reduce:duration-0
//     (mesmo padrão do resto do app, ver index.css/A3) — some controla a
//     própria animação de onda.
//   - o botão de trocar de modo não tinha estado de foco na referência
//     (só hover); aqui tem focus-visible:ring, como todo o resto do app.
// ---------------------------------------------------------------------------

const PANEL_COLORS = ["#0F2C59", "#1E3A8A", "#0369A1", "#1D4ED8"];

function Hero({ title, text, buttonLabel, onSwitch }) {
  return (
    <div className="relative z-10 flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-white lg:gap-4 lg:p-10">
      <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-white/15">
        <Stethoscope className="h-5 w-5" aria-hidden="true" />
      </div>
      <div>
        <h2 className="font-head text-2xl font-bold">{title}</h2>
        <p className="mx-auto mt-2 max-w-xs text-sm leading-relaxed text-blue-100">{text}</p>
      </div>
      <button type="button" onClick={onSwitch}
        className="rounded-full border border-white/30 bg-white/10 px-8 py-2.5 text-sm font-medium text-white backdrop-blur-sm transition-colors hover:bg-white hover:text-[#0F2C59] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0F2C59]">
        {buttonLabel}
      </button>
    </div>
  );
}

/**
 * `mode`: "login" | "register". `hero`: { title, text, buttonLabel } do modo
 * ATUAL (o convite é sempre para o OUTRO modo — ver pages/Login.js, HERO_COPY).
 * `onSwitch`: troca de modo. `formTitle`/`formSubtitle`: cabeçalho do painel
 * de formulário (recebe o foco ao trocar de modo — región SPA, igual ao
 * resto do app: ver RouteAnnouncer.js). `children`: o formulário do modo atual.
 */
export default function AuthCard({ mode, hero, onSwitch, formTitle, formSubtitle, children }) {
  const isRegister = mode === "register";
  const headingRef = useRef(null);

  // Acessibilidade: ao trocar de modo, o conteúdo muda inteiro (outro
  // formulário) — o foco vai para o título do novo painel, como a troca de
  // tela faz com <main> (RouteAnnouncer.js), para quem usa teclado/leitor de
  // tela não continuar "no meio" do formulário anterior. Primeira carga não
  // move foco (mesma regra do RouteAnnouncer).
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [mode]);

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#F5F7FA] px-4 py-10">
      {/* Fundo da página: só um véu estático em degradê (sem WebGL) — o
          destaque animado fica no painel do cartão, abaixo. */}
      <div className="pointer-events-none absolute inset-0" aria-hidden="true" style={{
        background: "radial-gradient(1100px circle at 15% 10%, rgba(30,58,138,0.08), transparent 55%),"
          + "radial-gradient(900px circle at 85% 90%, rgba(3,105,161,0.08), transparent 55%)",
      }} />

      <div className={cn(
        "relative z-10 grid w-full max-w-4xl grid-cols-1 overflow-hidden rounded-[28px] bg-white shadow-2xl shadow-slate-900/10",
        "lg:min-h-[600px] lg:grid-cols-2",
      )}>
        {/* Painel decorativo: no celular é uma faixa normal (ordem 1); no
            computador, posição absoluta que desliza entre os lados. */}
        <div className={cn(
          "relative min-h-[220px] overflow-hidden",
          "lg:absolute lg:inset-y-0 lg:left-0 lg:w-1/2",
          "lg:transition-transform lg:duration-700 lg:ease-in-out motion-reduce:lg:duration-0",
          isRegister ? "lg:translate-x-0" : "lg:translate-x-full",
        )}>
          <div className="absolute inset-0 bg-[#0F2C59]">
            <GradientWave colors={PANEL_COLORS} shadowPower={6} />
          </div>
          <div key={mode} className="psi-fade-up relative h-full">
            <Hero title={hero.title} text={hero.text} buttonLabel={hero.buttonLabel} onSwitch={onSwitch} />
          </div>
        </div>

        {/* Painel do formulário: ocupa sempre o lado OPOSTO ao decorativo. */}
        <div className={cn(
          "relative order-2 flex flex-col justify-center overflow-y-auto p-6 sm:p-10",
          "lg:absolute lg:inset-y-0 lg:left-0 lg:order-none lg:w-1/2",
          "lg:transition-transform lg:duration-700 lg:ease-in-out motion-reduce:lg:duration-0",
          isRegister ? "lg:translate-x-full" : "lg:translate-x-0",
        )}>
          <div key={mode} className="psi-fade-up mx-auto w-full max-w-sm">
            <h1 ref={headingRef} tabIndex={-1} className="font-head text-2xl font-bold text-[#0F172A] focus:outline-none">
              {formTitle}
            </h1>
            {formSubtitle && <p className="mt-1 text-sm text-slate-500">{formSubtitle}</p>}
            <div className="mt-6">{children}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
