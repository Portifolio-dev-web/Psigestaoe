import { createContext, useCallback, useContext, useEffect, useRef } from "react";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

// ---------------------------------------------------------------------------
// PRODUTO: bloqueio por inatividade (lado do navegador).
//
// Em consultório o computador fica aberto na frente do paciente ou da
// recepção. Depois de N minutos sem uso (Configurações: 5/10/15/30), a tela
// trava e os dados somem até a pessoa confirmar a identidade.
//
// O que este provedor faz:
//   - registra uso real da tela (mouse, teclado, toque, rolagem), no máximo
//     a cada 5 s para não pesar;
//   - soma a atividade de TODAS as abas abertas (BroadcastChannel): trabalhar
//     numa aba não deixa a outra bloquear a sessão, que é a mesma;
//   - avisa o servidor que há uso ("sinal de vida", 1x por minuto no máximo).
//     O servidor bloqueia sozinho se os avisos pararem (ver server.py);
//   - ao passar do tempo, trava a tela NA HORA e avisa o servidor;
//   - confere de novo quando a aba volta a ficar visível ou ganha foco: um
//     notebook que hibernou trava assim que acorda.
// A tela de bloqueio em si fica em components/LockScreen.js.
//
// Acessibilidade (análise de UI, A13 e F5): AVISO antes de bloquear. Antes a
// tela travava sem aviso, por exemplo no meio da escrita de uma evolução
// (nada se perdia, mas assustava). Agora, 1 minuto antes, aparece um aviso
// com "Continuar usando"; qualquer uso da tela (mouse, tecla, toque) também
// adia o bloqueio e fecha o aviso. Um minuto dá tempo de sobra para quem usa
// leitor de tela ou teclado responder (a WCAG pede pelo menos 20 s).
// ---------------------------------------------------------------------------

const ACTIVITY_EVENTS = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"];
const ACTIVITY_THROTTLE_MS = 5000;
// 5 s (antes 10 s): o aviso de 1 minuto aparece no máximo 5 s atrasado.
const CHECK_INTERVAL_MS = 5000;
const WARNING_BEFORE_MS = 60000;
const WARNING_TOAST_ID = "idle-lock-warning";
const HEARTBEAT_INTERVAL_MS = 60000;
const ACTIVITY_CHANNEL = "psi-activity";

const IdleLockContext = createContext({ lockNow: () => {} });

export function IdleLockProvider({ children }) {
  const { user, markLocked } = useAuth();
  const locked = Boolean(user?.locked);
  const lockMinutes = user?.lock_minutes || 10;
  const lastActivityRef = useRef(Date.now());
  const lastHeartbeatRef = useRef(Date.now());
  const lockingRef = useRef(false);
  // A13: o aviso de "vai bloquear em 1 minuto" está na tela?
  const warnedRef = useRef(false);

  const dismissWarning = useCallback(() => {
    if (!warnedRef.current) return;
    warnedRef.current = false;
    toast.dismiss(WARNING_TOAST_ID);
  }, []);

  const lockNow = useCallback(async (reason = "manual") => {
    if (lockingRef.current) return;
    lockingRef.current = true;
    markLocked(); // a tela trava antes mesmo da resposta do servidor
    try {
      await api.post("/auth/lock", { reason });
    } catch (e) {
      // 423/401 já são tratados pela api.js; a tela continua travada.
    } finally {
      lockingRef.current = false;
    }
  }, [markLocked]);

  // Registro de uso da tela (desligado enquanto bloqueada).
  useEffect(() => {
    if (locked) return undefined;
    let lastRecorded = 0;
    let channel = null;
    if (typeof window.BroadcastChannel === "function") {
      channel = new window.BroadcastChannel(ACTIVITY_CHANNEL);
      channel.onmessage = (event) => {
        if (event.data?.type === "activity") {
          lastActivityRef.current = Math.max(lastActivityRef.current, event.data.at);
          dismissWarning(); // uso em OUTRA aba também adia o bloqueio
        }
      };
    }
    const onActivity = () => {
      const now = Date.now();
      dismissWarning(); // A13: usou a tela, o bloqueio foi adiado (antes do limite de frequência abaixo)
      if (now - lastRecorded < ACTIVITY_THROTTLE_MS) return;
      lastRecorded = now;
      lastActivityRef.current = now;
      channel?.postMessage({ type: "activity", at: now });
    };
    ACTIVITY_EVENTS.forEach((name) => window.addEventListener(name, onActivity, { passive: true, capture: true }));
    return () => {
      ACTIVITY_EVENTS.forEach((name) => window.removeEventListener(name, onActivity, { capture: true }));
      channel?.close();
    };
  }, [locked, dismissWarning]);

  // Relógio da inatividade + sinal de vida para o servidor.
  useEffect(() => {
    if (locked) return undefined;
    lastActivityRef.current = Date.now(); // abrir o sistema (ou desbloquear) conta como uso

    const check = () => {
      const now = Date.now();
      const remaining = lockMinutes * 60000 - (now - lastActivityRef.current);
      if (remaining <= 0) {
        dismissWarning();
        lockNow("idle");
        return;
      }
      // A13: falta 1 minuto ou menos -> avisa (uma vez), com "Continuar usando".
      if (remaining <= WARNING_BEFORE_MS && !warnedRef.current) {
        warnedRef.current = true;
        toast.warning("Por inatividade, a tela será bloqueada em 1 minuto.", {
          id: WARNING_TOAST_ID,
          description: "Use a tela (mouse, teclado ou toque) ou clique em “Continuar usando”.",
          duration: Infinity, // fica até a pessoa usar a tela ou a tela bloquear
          action: {
            label: "Continuar usando",
            onClick: () => {
              lastActivityRef.current = Date.now();
              warnedRef.current = false;
            },
          },
        });
      }
      if (lastActivityRef.current > lastHeartbeatRef.current && now - lastHeartbeatRef.current >= HEARTBEAT_INTERVAL_MS) {
        lastHeartbeatRef.current = now;
        api.post("/auth/activity").catch(() => {}); // 423 aqui = servidor já bloqueou (api.js mostra a tela)
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };

    const interval = setInterval(check, CHECK_INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", check);
    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", check);
    };
  }, [locked, lockMinutes, lockNow, dismissWarning]);

  return <IdleLockContext.Provider value={{ lockNow }}>{children}</IdleLockContext.Provider>;
}

export const useIdleLock = () => useContext(IdleLockContext);
