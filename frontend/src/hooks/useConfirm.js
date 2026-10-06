import { useCallback, useRef, useState } from "react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

// ---------------------------------------------------------------------------
// Confirmação no padrão do sistema (análise de UI, F2).
//
// Três confirmações usavam window.confirm: a janela do NAVEGADOR, com outra
// aparência, botões "OK/Cancelar" e, em alguns navegadores, o endereço do
// site no título. Agora são diálogos do próprio sistema (o mesmo de "Excluir
// paciente?"), com título claro, botões que dizem o que fazem e o foco
// devolvido ao botão de origem ao fechar.
//
// Uso:
//   const [confirm, confirmDialog] = useConfirm();
//   if (!(await confirm({ title, description, confirmLabel, destructive: true }))) return;
//   ...e renderizar {confirmDialog} no componente.
// ---------------------------------------------------------------------------
export function useConfirm() {
  const [request, setRequest] = useState(null); // { title, description, confirmLabel, cancelLabel, destructive }
  const resolveRef = useRef(null);

  const confirm = useCallback((options) => new Promise((resolve) => {
    resolveRef.current = resolve;
    setRequest(options);
  }), []);

  // Só a PRIMEIRA resposta vale (o botão de confirmar também fecha o diálogo).
  const answer = (value) => {
    resolveRef.current?.(value);
    resolveRef.current = null;
    setRequest(null);
  };

  const dialog = (
    <AlertDialog open={Boolean(request)} onOpenChange={(open) => { if (!open) answer(false); }}>
      <AlertDialogContent data-testid="confirm-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>{request?.title}</AlertDialogTitle>
          <AlertDialogDescription>{request?.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => answer(false)}>{request?.cancelLabel || "Cancelar"}</AlertDialogCancel>
          <AlertDialogAction onClick={() => answer(true)} data-testid="confirm-dialog-ok"
            className={request?.destructive ? "bg-red-600 hover:bg-red-700" : "bg-[#1E3A8A] hover:bg-[#0F2C59]"}>
            {request?.confirmLabel || "Confirmar"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );

  return [confirm, dialog];
}
