import { createContext, useCallback, useContext, useEffect, useRef } from "react";
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
// ---------------------------------------------------------------------------

const ACTIVITY_EVENTS = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"];
const ACTIVITY_THROTTLE_MS = 5000;
const CHECK_INTERVAL_MS = 10000;
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
        }
      };
    }
    const onActivity = () => {
      const now = Date.now();
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
  }, [locked]);

  // Relógio da inatividade + sinal de vida para o servidor.
  useEffect(() => {
    if (locked) return undefined;
    lastActivityRef.current = Date.now(); // abrir o sistema (ou desbloquear) conta como uso

    const check = () => {
      const now = Date.now();
      if (now - lastActivityRef.current >= lockMinutes * 60000) {
        lockNow("idle");
        return;
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
  }, [locked, lockMinutes, lockNow]);

  return <IdleLockContext.Provider value={{ lockNow }}>{children}</IdleLockContext.Provider>;
}

export const useIdleLock = () => useContext(IdleLockContext);
