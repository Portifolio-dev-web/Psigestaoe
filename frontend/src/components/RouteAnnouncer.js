import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigationType } from "react-router-dom";
import { pageTitle } from "@/lib/pageTitles";
import { mainRegion } from "@/lib/focus";

// ---------------------------------------------------------------------------
// Acessibilidade (análise de UI, A1): troca de tela num app de página única.
//
// Num site comum, cada clique carrega uma página nova: o título muda, o foco
// volta ao topo e o leitor de tela lê a página. Aqui (React) só o conteúdo é
// trocado, e antes nada disso acontecia: o título era sempre o mesmo, o foco
// caía no início da página e o leitor de tela ficava em silêncio.
//
// Agora, a cada troca de tela (mudança de endereço, não de filtros da URL):
//   1. o título da aba passa a ser o da tela ("Pacientes · PsiGestão");
//   2. o foco vai para o conteúdo principal (<main>): o próximo Tab já é o
//      primeiro item da tela, sem passar de novo pelo menu;
//   3. uma região "ao vivo" (invisível) anuncia o nome da tela.
// Na abertura do sistema (primeira tela) só o título é definido: o leitor de
// tela já anuncia a página carregada sozinho.
//
// Espera a tela ficar pronta: as telas são carregadas sob demanda, e um menu
// ou diálogo que está fechando ainda esconde o conteúdo (aria-hidden). Se a
// nova tela abrir um diálogo por conta própria (ex.: "Registrar evolução"
// abre o editor), o foco fica no diálogo, e nada é feito aqui.
// ---------------------------------------------------------------------------
const READY_TIMEOUT_MS = 5000;
const POLL_MS = 50;

const dialogOpen = () => Boolean(document.querySelector('[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]'));

export default function RouteAnnouncer() {
  const { pathname } = useLocation();
  const navigationType = useNavigationType();
  const [message, setMessage] = useState("");
  const firstRender = useRef(true);

  useEffect(() => {
    const title = pageTitle(pathname);
    document.title = title.full;
    if (firstRender.current) {
      firstRender.current = false;
      return undefined;
    }
    // Ir para outra tela começa do topo; "Voltar" do navegador (POP) mantém a rolagem.
    if (navigationType !== "POP") window.scrollTo(0, 0);

    let timer;
    const started = Date.now();
    const settle = () => {
      const main = document.querySelector("main");
      const ready = main && !main.closest('[aria-hidden="true"]') && !dialogOpen();
      if (!ready && Date.now() - started < READY_TIMEOUT_MS) {
        timer = setTimeout(settle, POLL_MS);
        return;
      }
      if (!ready) return; // um diálogo da nova tela ficou com o foco
      mainRegion()?.focus({ preventScroll: true });
      // Limpa e preenche de novo: duas telas com o mesmo nome (duas fichas)
      // também são anunciadas.
      setMessage("");
      timer = setTimeout(() => setMessage(title.name || title.full), 100);
    };
    settle();
    return () => clearTimeout(timer);
  }, [pathname, navigationType]);

  return (
    <div className="sr-only" aria-live="polite" aria-atomic="true" data-testid="route-announcer">
      {message}
    </div>
  );
}
