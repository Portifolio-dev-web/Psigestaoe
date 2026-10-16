import { Component, createRef } from "react";
import { TriangleAlert } from "lucide-react";
import { captureException } from "@/lib/sentry";

// ---------------------------------------------------------------------------
// DEPLOY (varredura, item D1): antes, um erro de JavaScript não tratado em
// QUALQUER tela deixava a página em branco, sem nenhuma mensagem — pior
// momento possível para acontecer no meio de um atendimento. Error boundary
// do React só funciona como classe (não existe equivalente em hook até hoje).
//
// Fica FORA do react-router (ver index.js: envolve o <App/> inteiro, antes do
// <BrowserRouter> que mora dentro dele) — um erro bem no topo da árvore
// também precisa de rede de segurança, e assim os botões abaixo usam
// navegação de verdade (<a href>, location.reload()), não o router: se o
// estado em memória estiver corrompido, um recarregamento de página de
// verdade é mais confiável que continuar dentro do mesmo React.
//
// Nunca mostra error.message/stack na tela: pode conter o que a função que
// quebrou estava processando (ex.: texto de prontuário já descriptografado
// numa variável) — a mesma razão do backend nunca mandar "variáveis locais"
// ao Sentry (observability.py, include_local_variables=False). O detalhe vai
// só para o Sentry (se configurado), nunca para a tela.
// ---------------------------------------------------------------------------
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
    this.headingRef = createRef();
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    captureException(error, info);
  }

  componentDidUpdate(_prevProps, prevState) {
    // Acessibilidade: quando a tela de erro aparece, o foco vai para o título
    // dela (mesma lógica do RouteAnnouncer ao trocar de tela) — sem isso,
    // quem usa teclado/leitor de tela fica "preso" onde estava, sem saber
    // que a tela toda mudou.
    if (this.state.hasError && !prevState.hasError) {
      this.headingRef.current?.focus();
    }
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#F8FAFC] px-4">
        <div className="w-full max-w-sm text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-amber-50 text-amber-700">
            <TriangleAlert className="h-7 w-7" aria-hidden="true" />
          </div>
          <h1 ref={this.headingRef} tabIndex={-1}
            className="mt-5 font-head text-2xl font-bold text-[#0F172A] focus:outline-none">
            Algo deu errado
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-slate-500">
            A tela encontrou um problema inesperado. Nada que você já salvou foi perdido — recarregar a página
            costuma resolver.
          </p>
          <div className="mt-6 flex flex-col gap-2">
            <button type="button" onClick={() => window.location.reload()}
              className="min-h-10 rounded-md bg-[#1E3A8A] px-4 text-sm font-medium text-white hover:bg-[#0F2C59] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A] focus-visible:ring-offset-2">
              Recarregar a página
            </button>
            <a href="/dashboard"
              className="flex min-h-10 items-center justify-center rounded-md border border-slate-300 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A] focus-visible:ring-offset-2">
              Ir para o início
            </a>
          </div>
        </div>
      </div>
    );
  }
}
