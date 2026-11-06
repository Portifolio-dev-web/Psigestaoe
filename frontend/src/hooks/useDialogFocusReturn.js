import { useCallback, useRef } from "react";
import { restoreFocus } from "@/lib/focus";

// ---------------------------------------------------------------------------
// Acessibilidade (análise de UI, A2): ao fechar um diálogo, o foco volta para
// o botão que o abriu.
//
// O Radix só faz isso quando o diálogo é aberto por um <DialogTrigger>. Os
// diálogos do sistema são abertos por código (open={...}) a partir de botões
// comuns, e aí o Radix mandava o foco para o início da página: depois de
// salvar um paciente, quem usa teclado recomeçava a navegação pelo menu.
//
// Como funciona: ao abrir (onOpenAutoFocus, que roda ANTES de o foco entrar no
// diálogo), guarda o elemento que tinha o foco; ao fechar (onCloseAutoFocus),
// devolve o foco a ele (ou ao conteúdo principal, se ele sumiu). Se a tela
// mudou com o diálogo aberto (ex.: "Registrar evolução" leva à ficha), quem
// cuida do foco é o RouteAnnouncer, e nada é feito aqui.
// Usado pelos componentes compartilhados dialog.jsx, alert-dialog.jsx e sheet.jsx.
// ---------------------------------------------------------------------------

// Diálogo aberto por um item de menu (ex.: "⋮ > Editar" na lista de pacientes):
// o item some quando o menu fecha, então o foco volta ao botão que abriu o MENU.
// O Radix liga o menu ao botão por aria-labelledby.
function focusOrigin() {
  const active = document.activeElement;
  const menu = active?.closest?.('[role="menu"]');
  const trigger = menu && document.getElementById(menu.getAttribute("aria-labelledby") || "");
  return trigger || active;
}

export function useDialogFocusReturn({ onOpenAutoFocus, onCloseAutoFocus } = {}) {
  const originRef = useRef(null);
  const pathRef = useRef("");

  const handleOpenAutoFocus = useCallback((event) => {
    originRef.current = focusOrigin();
    pathRef.current = window.location.pathname;
    onOpenAutoFocus?.(event);
  }, [onOpenAutoFocus]);

  const handleCloseAutoFocus = useCallback((event) => {
    onCloseAutoFocus?.(event);
    if (event.defaultPrevented) return; // quem usa o diálogo decidiu o foco
    event.preventDefault(); // impede o Radix de mandar o foco para o início da página
    // Acessibilidade (auditoria UX/A11Y, A2): neste momento o Radix ainda não
    // tirou o aria-hidden que ele mesmo põe nos elementos-irmãos do diálogo
    // enquanto ele está aberto — ele só tira depois que a animação de saída
    // termina (~200ms, ver dialog.jsx/sheet.jsx). Um requestAnimationFrame só
    // não bastava (ainda estava escondido); por isso espera em pequenos
    // intervalos até o botão original deixar de estar "escondido" (ou até um
    // limite, para nunca travar o foco caso algo dê errado).
    const path = pathRef.current;
    const origin = originRef.current;
    const deadline = Date.now() + 500;
    const tryRestore = () => {
      const stillHidden = origin?.closest?.("[aria-hidden='true'], [inert]");
      if (stillHidden && Date.now() < deadline) {
        setTimeout(tryRestore, 20);
        return;
      }
      if (window.location.pathname !== path) return;
      restoreFocus(origin);
    };
    tryRestore();
  }, [onCloseAutoFocus]);

  return { onOpenAutoFocus: handleOpenAutoFocus, onCloseAutoFocus: handleCloseAutoFocus };
}
