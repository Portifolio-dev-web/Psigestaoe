// ---------------------------------------------------------------------------
// Acessibilidade (análise de UI, A1 e A2): para onde vai o foco do teclado.
//
// Quem usa teclado ou leitor de tela "está" no elemento com foco. Se o foco
// cai no início da página (document.body), a pessoa perde o lugar: o leitor
// de tela não diz nada, e o próximo Tab recomeça do menu lateral.
// ---------------------------------------------------------------------------

/** Conteúdo principal da tela (<main>), pronto para receber foco por código. */
export function mainRegion() {
  const main = document.querySelector("main");
  // tabindex="-1": recebe foco por código, mas não entra na ordem do Tab.
  if (main && !main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
  return main;
}

/** Leva o foco a `element`; se ele não existir mais (ex.: o item do menu que
 *  abriu o diálogo sumiu), ao conteúdo principal, nunca ao início da página. */
export function restoreFocus(element) {
  const usable = element && element !== document.body && element.isConnected && !element.disabled
    && !element.closest("[inert], [aria-hidden='true']");
  const target = usable ? element : mainRegion();
  target?.focus({ preventScroll: true });
}
