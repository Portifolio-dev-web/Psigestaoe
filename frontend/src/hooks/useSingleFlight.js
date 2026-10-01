import { useCallback, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// SEGURANÇA (varredura, item 9): "uma de cada vez" para ações que gravam.
//
// Antes, algumas ações (arquivar, anonimizar, excluir, exportar, invalidar
// rascunhos) não travavam o botão durante o envio: um duplo clique, ou um
// segundo clique impaciente, mandava a mesma ação duas vezes (duas
// exportações, dois registros de auditoria, dois avisos).
//
// run(chave, ação): executa a ação só se não houver outra com a MESMA chave
// em andamento; um clique repetido é ignorado. A trava fica numa referência
// (useRef), e não só no estado do React, para valer mesmo antes de a tela
// redesenhar o botão desabilitado.
// isBusy(chave): para desabilitar o botão e mostrar o "carregando".
// Chaves por item (ex.: `patient:${id}`) travam só aquele item.
// ---------------------------------------------------------------------------
export function useSingleFlight() {
  const running = useRef(new Set());
  const [busy, setBusy] = useState(() => new Set());

  const run = useCallback(async (key, action) => {
    if (running.current.has(key)) return undefined;
    running.current.add(key);
    setBusy(new Set(running.current));
    try {
      return await action();
    } finally {
      running.current.delete(key);
      setBusy(new Set(running.current));
    }
  }, []);

  const isBusy = useCallback((key) => busy.has(key), [busy]);

  return { run, isBusy };
}
