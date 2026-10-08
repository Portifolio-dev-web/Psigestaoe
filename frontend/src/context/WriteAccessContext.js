import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Lock } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { READ_ONLY_ALLOWED, READ_ONLY_REASON, SUBSCRIPTION_PATH } from "@/lib/subscription";

// ---------------------------------------------------------------------------
// PRODUTO (assinatura): a conta pode GRAVAR (criar/editar)?
//
// No modo somente leitura (teste encerrado, atraso além da tolerância,
// assinatura encerrada) o servidor recusa criar paciente, evolução e sessão
// (402, `read_only`). Esperar o erro para explicar seria ruim: a pessoa
// preencheria um formulário inteiro para nada. Por isso as telas usam
// `guard(acao)`: com gravação liberada, a ação roda; sem, aparece na hora uma
// explicação com o caminho para assinar, e nada é aberto.
//
// O servidor continua sendo a regra de verdade (um botão esquecido aqui ainda
// recebe 402); quando isso acontece, lib/api.js avisa (evento psi:read-only),
// o AuthContext atualiza a situação e esta explicação também aparece.
// Fora do sistema (página pública de Ajuda) o padrão é "pode gravar".
// ---------------------------------------------------------------------------
const WriteAccessContext = createContext({ canWrite: true, subscription: null, guard: (fn) => fn, explain: () => {} });

export function WriteAccessProvider({ children }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const subscription = user?.subscription || null;
  const canWrite = subscription ? subscription.can_write : true;
  const [open, setOpen] = useState(false);

  const explain = useCallback(() => setOpen(true), []);
  const guard = useCallback((fn) => (...args) => {
    if (!canWrite) {
      setOpen(true);
      return undefined;
    }
    return fn(...args);
  }, [canWrite]);

  // O servidor recusou uma gravação (lib/api.js): explica, se a tela não guardou antes.
  useEffect(() => {
    window.addEventListener("psi:read-only", explain);
    return () => window.removeEventListener("psi:read-only", explain);
  }, [explain]);

  const reason = READ_ONLY_REASON[subscription?.reason] || "sua conta está sem assinatura ativa";

  return (
    <WriteAccessContext.Provider value={{ canWrite, subscription, guard, explain }}>
      {children}
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent data-testid="read-only-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Lock className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" /> Modo somente leitura
            </AlertDialogTitle>
            <AlertDialogDescription>
              Não dá para registrar agora porque {reason}. {READ_ONLY_ALLOWED} Para voltar a registrar, assine um plano.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Agora não</AlertDialogCancel>
            <AlertDialogAction onClick={() => navigate(SUBSCRIPTION_PATH)} className="bg-[#1E3A8A] hover:bg-[#0F2C59]"
              data-testid="read-only-see-plans">
              Ver planos
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </WriteAccessContext.Provider>
  );
}

export const useWriteAccess = () => useContext(WriteAccessContext);
