import { createContext, useContext, useState, useEffect, useCallback, useRef } from "react";
import api from "@/lib/api";
import { forgetDraftKey } from "@/lib/drafts";

const AuthContext = createContext(null);

// PRODUTO: canal entre as abas do MESMO navegador. A sessão no servidor é uma
// só; se ela for bloqueada, desbloqueada ou encerrada numa aba, as outras
// precisam refletir isso na hora (senão uma aba esquecida aberta continuaria
// mostrando prontuários depois do bloqueio).
const SESSION_CHANNEL = "psi-session";

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const channelRef = useRef(null);

  const checkAuth = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
    } catch (e) {
      setUser(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Conformidade (item 16): api.js dispara este evento quando o backend exige
  // o aceite da versão atual da política; recarregar o usuário faz o
  // ProtectedRoute mostrar a tela de aceite.
  useEffect(() => {
    window.addEventListener("psi:terms-required", checkAuth);
    // Produto (assinatura): o servidor recusou uma gravação (somente leitura):
    // a situação da conta mudou (ex.: o teste acabou com a tela aberta).
    window.addEventListener("psi:read-only", checkAuth);
    return () => {
      window.removeEventListener("psi:terms-required", checkAuth);
      window.removeEventListener("psi:read-only", checkAuth);
    };
  }, [checkAuth]);

  const broadcast = useCallback((message) => {
    try {
      channelRef.current?.postMessage(message);
    } catch (e) {
      // Canal indisponível: as outras abas descobrem pelo 423/401 do servidor.
    }
  }, []);

  // Bloqueio/desbloqueio vindos desta aba (tela de bloqueio, botão "Bloquear agora").
  const markLocked = useCallback(() => {
    setUser((current) => (current ? { ...current, locked: true } : current));
    broadcast({ type: "locked" });
  }, [broadcast]);

  const markUnlocked = useCallback((unlockedUser) => {
    setUser(unlockedUser);
    broadcast({ type: "unlocked" });
  }, [broadcast]);

  // Sessão expirada/encerrada (401 no meio do uso): volta ao login. A chave
  // dos rascunhos NÃO é esquecida aqui (ver lib/drafts.js, forgetDraftKey).
  const endSession = useCallback(() => {
    setUser((current) => (current ? false : current));
  }, []);

  useEffect(() => {
    if (typeof window.BroadcastChannel !== "function") return undefined;
    const channel = new window.BroadcastChannel(SESSION_CHANNEL);
    channelRef.current = channel;
    channel.onmessage = (event) => {
      const type = event.data?.type;
      if (type === "locked") setUser((current) => (current ? { ...current, locked: true } : current));
      else if (type === "unlocked") checkAuth(); // confirma no servidor antes de mostrar dados
      else if (type === "logout") {
        forgetDraftKey(); // logout explícito em outra aba
        endSession();
      }
    };
    return () => {
      channel.close();
      channelRef.current = null;
    };
  }, [checkAuth, endSession]);

  // Eventos disparados pela api.js (ver lib/api.js).
  useEffect(() => {
    const onLocked = () => markLocked();
    window.addEventListener("psi:session-locked", onLocked);
    window.addEventListener("psi:session-expired", endSession);
    return () => {
      window.removeEventListener("psi:session-locked", onLocked);
      window.removeEventListener("psi:session-expired", endSession);
    };
  }, [markLocked, endSession]);

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch (e) {}
    broadcast({ type: "logout" });
    forgetDraftKey();
    setUser(false);
    window.location.href = "/login";
  };

  return (
    <AuthContext.Provider value={{ user, setUser, loading, checkAuth, logout, markLocked, markUnlocked }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
