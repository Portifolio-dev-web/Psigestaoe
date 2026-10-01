import { useCallback, useEffect, useRef, useState } from "react";
import { deleteDraft, draftsSupported, saveDraft } from "@/lib/drafts";

// PRODUTO: salva o rascunho automaticamente enquanto a pessoa escreve.
//
// - Grava 1,2 s depois da última tecla (não a cada tecla: criptografar e
//   gravar em todo caractere seria desperdício).
// - Grava na hora quando a aba é escondida (troca de aba, minimizar, celular
//   bloqueado) e quando o editor é fechado.
// - Se ainda houver algo não gravado ao fechar a aba, o navegador pergunta
//   antes de sair.
// - `dirty` vem de quem usa o hook: só existe rascunho quando o texto difere
//   do original (abrir uma anotação para ler não cria rascunho).
// Estados: idle | pending | saving | saved | error | unsupported.
const SAVE_DELAY_MS = 1200;

export function useDraftAutosave({ slot, payload, dirty, enabled }) {
  const supported = draftsSupported();
  const [status, setStatus] = useState(supported ? "idle" : "unsupported");
  const [savedAt, setSavedAt] = useState(null);
  const pendingRef = useRef(null);
  const timerRef = useRef(null);
  // Muda a cada descarte. Uma gravação que termina DEPOIS de um descarte
  // (ex.: a anotação acabou de ser salva no servidor) apaga o que gravou,
  // em vez de ressuscitar um rascunho que não deve mais existir.
  const generationRef = useRef(0);

  const persist = useCallback(async () => {
    clearTimeout(timerRef.current);
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    const generation = generationRef.current;
    setStatus("saving");
    try {
      const at = await saveDraft(pending.slot, pending.payload);
      if (generation !== generationRef.current) {
        deleteDraft(pending.slot);
        return;
      }
      setSavedAt(at);
      setStatus("saved");
    } catch (e) {
      setStatus("error");
    }
  }, []);

  // Comparação pelo conteúdo: objetos novos com o mesmo texto não regravam.
  const payloadKey = JSON.stringify(payload);
  useEffect(() => {
    if (!supported || !enabled || !slot || !dirty) return;
    pendingRef.current = { slot, payload: JSON.parse(payloadKey) };
    setStatus("pending");
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(persist, SAVE_DELAY_MS);
  }, [supported, enabled, slot, dirty, payloadKey, persist]);

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") persist();
    };
    const onBeforeUnload = (event) => {
      if (!pendingRef.current) return;
      persist();
      event.preventDefault();
      event.returnValue = ""; // pede confirmação ao fechar a aba com texto não gravado
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [persist]);

  // Editor fechado/desmontado: grava o que estiver pendente.
  useEffect(() => () => { persist(); }, [persist]);

  /** Apaga o rascunho (anotação enviada ao servidor, ou "Descartar rascunho"). */
  const discard = useCallback(() => {
    generationRef.current += 1;
    clearTimeout(timerRef.current);
    pendingRef.current = null;
    if (slot) deleteDraft(slot);
    setSavedAt(null);
    setStatus(supported ? "idle" : "unsupported");
  }, [slot, supported]);

  return { status, savedAt, flush: persist, discard, supported };
}
