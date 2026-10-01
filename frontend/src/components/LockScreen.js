import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { GoogleLogin } from "@react-oauth/google";
import { toast } from "sonner";
import { Loader2, Lock } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { GradientWave } from "@/components/ui/gradient-wave";

// ---------------------------------------------------------------------------
// PRODUTO: tela de bloqueio por inatividade.
//
// - Cobre TUDO com um fundo opaco (sem desfoque: desfoque ainda deixa ver o
//   formato de nomes e textos). O app por baixo fica `inert` (ver App.js):
//   teclado e leitor de tela não alcançam o conteúdo oculto.
// - O que estava aberto continua montado por baixo: uma anotação sendo
//   escrita, um filtro, a página atual. Desbloqueou, está tudo como estava.
// - Desbloqueia com a senha; quem entra só pelo Google, com o Google (o
//   servidor confere que é A MESMA conta). Na 5ª senha errada o servidor
//   encerra a sessão e o app volta ao login.
// ---------------------------------------------------------------------------

// Diálogos, menus e listas suspensas (Radix) são desenhados FORA do #root,
// direto no <body>. Por isso não basta deixar o app inerte (App.js): uma
// anotação aberta num diálogo continuaria acessível, prenderia o foco do
// teclado (a senha digitada ia parar dentro da anotação!) e deixaria a página
// com pointer-events: none (o campo de senha não receberia cliques). A tela de
// bloqueio vai para o <body> por último e deixa TODO o resto inerte, sem
// fechar nada: ao desbloquear, o diálogo e o texto continuam lá.
function useInertSiblings(container) {
  useLayoutEffect(() => {
    document.body.appendChild(container);
    const others = [...document.body.children].filter((el) => el !== container);
    const previous = others.map((el) => ({ el, inert: el.hasAttribute("inert"), ariaHidden: el.getAttribute("aria-hidden") }));
    others.forEach((el) => {
      el.setAttribute("inert", "");
      el.setAttribute("aria-hidden", "true");
    });
    return () => {
      // Restaura exatamente o estado anterior (o Radix também usa aria-hidden).
      previous.forEach(({ el, inert, ariaHidden }) => {
        if (!inert) el.removeAttribute("inert");
        if (ariaHidden === null) el.removeAttribute("aria-hidden");
        else el.setAttribute("aria-hidden", ariaHidden);
      });
      container.remove();
    };
  }, [container]);
}

// Mesmas cores da tela de login (constante de módulo: não recria o WebGL).
const LOCK_GRADIENT_COLORS = ["#0F2C59", "#1E3A8A", "#0369A1", "#1D4ED8"];

export default function LockScreen() {
  const { user, markUnlocked, logout } = useAuth();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);
  const overlayRef = useRef(null);
  const [container] = useState(() => document.createElement("div"));
  useInertSiblings(container);

  useEffect(() => {
    inputRef.current?.focus();
    // Um diálogo aberto por baixo "ouve" o documento inteiro: um clique fora
    // dele o fecharia, e um foco fora dele seria puxado de volta. Os eventos
    // da tela de bloqueio param aqui e não chegam a ele.
    const overlay = overlayRef.current;
    const stop = (event) => event.stopPropagation();
    ["pointerdown", "mousedown", "focusin"].forEach((name) => overlay?.addEventListener(name, stop));
    return () => ["pointerdown", "mousedown", "focusin"].forEach((name) => overlay?.removeEventListener(name, stop));
  }, []);

  const unlock = async (body) => {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post("/auth/unlock", body);
      setPassword("");
      markUnlocked(data.user);
    } catch (err) {
      const message = formatApiErrorDetail(err.response?.data?.detail);
      if (err.response?.status === 401) {
        toast.error(message); // sessão encerrada: o AuthContext leva ao login
      } else {
        setError(message);
        setPassword("");
        inputRef.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  };

  const initials = (user?.name || user?.email || "P").split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();

  return createPortal(
    // pointer-events: auto -> recebe cliques mesmo com o <body> travado por um diálogo aberto.
    <div ref={overlayRef} className="fixed inset-0 z-[100] flex items-center justify-center bg-[#0F2C59] p-4"
      style={{ pointerEvents: "auto" }}
      role="dialog" aria-modal="true" aria-labelledby="lock-title" data-testid="lock-screen">
      <GradientWave colors={LOCK_GRADIENT_COLORS} />
      <div className="absolute inset-0 bg-[#0F2C59]/40" />

      <Card className="relative z-10 w-full max-w-sm border-0 p-6 shadow-xl sm:p-8">
        <div className="flex flex-col items-center text-center">
          <Avatar className="h-14 w-14">
            <AvatarImage src={user?.picture} alt="" />
            <AvatarFallback className="bg-[#1E3A8A] text-white">{initials}</AvatarFallback>
          </Avatar>
          <p className="mt-3 font-medium text-[#0F172A]">{user?.name || "Profissional"}</p>
          <p className="text-xs text-slate-500">{user?.email}</p>

          <div className="mt-5 flex items-center gap-2 text-[#1E3A8A]">
            <Lock className="h-4 w-4" />
            <h1 id="lock-title" className="font-head text-lg font-bold">Tela bloqueada</h1>
          </div>
          <p className="mt-1 text-sm text-slate-500">
            Para proteger os dados dos pacientes, confirme sua identidade para continuar de onde parou.
          </p>
        </div>

        {user?.has_password ? (
          <form className="mt-5 space-y-3" onSubmit={(e) => { e.preventDefault(); unlock({ password }); }}>
            <div>
              <Label htmlFor="unlock-password" className="text-slate-700">Senha</Label>
              <Input id="unlock-password" ref={inputRef} type="password" autoComplete="current-password"
                value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1.5"
                aria-invalid={Boolean(error)} aria-describedby={error ? "unlock-error" : undefined}
                data-testid="unlock-password" />
            </div>
            {error && <p id="unlock-error" role="alert" className="text-sm text-red-600">{error}</p>}
            <Button type="submit" disabled={busy || !password} className="w-full bg-[#1E3A8A] hover:bg-[#0F2C59]"
              data-testid="unlock-submit">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Desbloquear"}
            </Button>
          </form>
        ) : (
          <div className="mt-5 flex flex-col items-center gap-2">
            <GoogleLogin
              onSuccess={(response) => unlock({ credential: response.credential })}
              onError={() => setError("Não foi possível confirmar com o Google. Tente novamente.")}
              shape="rectangular" size="large" text="continue_with"
            />
            {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
          </div>
        )}

        <button type="button" onClick={logout}
          className="mt-5 w-full text-center text-xs text-slate-500 hover:text-[#1E3A8A] hover:underline">
          Não é você? Sair e entrar com outra conta
        </button>
      </Card>
    </div>,
    container,
  );
}
