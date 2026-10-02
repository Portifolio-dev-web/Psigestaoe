import { Stethoscope } from "lucide-react";
import { GradientWave } from "@/components/ui/gradient-wave";

// ---------------------------------------------------------------------------
// Moldura das telas de entrada (login, cadastro, criar senha, esqueci minha
// senha, nova senha): painel da marca à esquerda (só no computador) e o
// formulário à direita.
//
// - Antes cada tela repetia este layout, e o cadastro ainda carregava uma
//   foto do Unsplash: um servidor de terceiros recebia o IP de cada pessoa
//   que abria a página. Agora todas usam o fundo animado local do login.
// - Acessibilidade (complementares): o formulário fica dentro de <main>
//   (região principal). Antes as telas públicas não tinham essa região, e o
//   leitor de tela não tinha como pular direto para o conteúdo.
// ---------------------------------------------------------------------------

// Cores da marca (fica no módulo: o fundo não é recriado a cada tecla digitada).
const GRADIENT_COLORS = ["#0F2C59", "#1E3A8A", "#0369A1", "#1D4ED8"];

export default function AuthShell({ headline, tagline, children }) {
  return (
    <div className="flex min-h-screen">
      {/* bg-[#0F2C59]: cor de reserva caso o navegador não tenha WebGL. */}
      <aside className="relative hidden w-1/2 overflow-hidden bg-[#0F2C59] lg:block" aria-label="PsiGestão">
        <GradientWave colors={GRADIENT_COLORS} />
        {/* Véu escuro leve: mantém o texto branco legível quando as ondas mais claras passam por trás. */}
        <div className="absolute inset-0 bg-[#0F2C59]/30" />
        <div className="relative z-10 flex h-full flex-col justify-between p-12 text-white">
          <div className="flex items-center gap-2.5">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-white/15">
              <Stethoscope className="h-5 w-5" aria-hidden="true" />
            </div>
            <span className="font-head text-xl font-bold">PsiGestão</span>
          </div>
          <div>
            <p className="font-head text-4xl font-extrabold leading-tight">{headline}</p>
            <p className="mt-4 max-w-md text-blue-100">{tagline}</p>
          </div>
          <p className="text-xs text-blue-200">Dados criptografados · HTTPS/TLS · CFP</p>
        </div>
      </aside>

      <main className="flex w-full items-center justify-center px-6 py-10 lg:w-1/2">
        <div className="psi-fade-up w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-2.5">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[#1E3A8A]">
                <Stethoscope className="h-5 w-5 text-white" aria-hidden="true" />
              </div>
              <span className="font-head text-xl font-bold text-[#0F172A]">PsiGestão</span>
            </div>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
