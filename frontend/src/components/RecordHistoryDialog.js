import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { History, Loader2 } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { formatDateTime } from "@/lib/datetime";
import { diffLines } from "@/lib/diff";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// UX (histórico de versões): toda edição de uma evolução guarda a versão
// anterior, mas antes não havia como vê-las na tela, só no banco ou na
// exportação. Aqui aparecem todas, da mais nova para a original, com:
//   - quando cada versão passou a valer e quando foi substituída;
//   - o que mudou em relação à versão anterior (linhas que saíram e entraram);
//   - o texto completo, se a pessoa preferir.
// Abrir o histórico fica registrado na trilha de auditoria (servidor).
// ---------------------------------------------------------------------------

export default function RecordHistoryDialog({ recordId, open, onOpenChange }) {
  const [data, setData] = useState(null);
  // A função de fechar fica numa referência: se o componente pai a recriar a
  // cada render, o efeito abaixo não deve buscar o histórico de novo por isso.
  const closeRef = useRef(onOpenChange);
  closeRef.current = onOpenChange;

  useEffect(() => {
    if (!open || !recordId) return;
    setData(null);
    api.get(`/records/${recordId}/versions`)
      .then(({ data: history }) => setData(history))
      .catch((err) => {
        toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Não foi possível abrir o histórico.");
        closeRef.current(false);
      });
  }, [open, recordId]);

  // Da mais nova para a mais antiga. Cada versão valeu de `since` até `until`:
  // a versão N passou a valer quando a N-1 foi substituída (a 1 nasceu com a evolução).
  const entries = [];
  if (data) {
    const all = [{ ...data.current, current: true }, ...data.versions];
    all.forEach((entry, index) => {
      const older = all[index + 1];
      entries.push({
        ...entry,
        since: older ? older.replaced_at : data.current.created_at,
        until: entry.current ? null : entry.replaced_at,
        older,
      });
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl" data-testid="record-history">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-head">
            <History className="h-5 w-5 text-[#1E3A8A]" aria-hidden="true" /> Histórico de versões
          </DialogTitle>
          <DialogDescription>
            Nada se perde ao editar uma evolução: cada versão anterior fica guardada. Em cada uma, as linhas
            em vermelho saíram e as em verde entraram em relação à versão anterior.
          </DialogDescription>
        </DialogHeader>
        {!data ? (
          <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" /></div>
        ) : (
          <ol className="space-y-4">
            {entries.map((entry) => <VersionEntry key={entry.version} entry={entry} />)}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  );
}

function VersionEntry({ entry }) {
  // Versão original não tem com o que comparar: mostra o texto completo.
  const [showFull, setShowFull] = useState(!entry.older);
  const changes = entry.older ? diffLines(entry.older.content, entry.content) : null;
  const diagnosisChanged = entry.older && (entry.older.diagnosis || "") !== (entry.diagnosis || "");

  return (
    <li className="rounded-md border border-slate-200 p-4" data-testid={`version-${entry.version}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-head text-sm font-bold text-[#0F172A]">Versão {entry.version}</h3>
        {entry.current && <Badge variant="secondary" className="text-[10px]">Atual</Badge>}
        {!entry.older && <Badge className="bg-slate-100 text-[10px] text-slate-700 hover:bg-slate-100">Original</Badge>}
      </div>
      <p className="mt-1 text-xs text-slate-500">
        Valeu de {formatDateTime(entry.since)}
        {entry.until ? ` até ${formatDateTime(entry.until)}` : " até agora"}
        {" · "}sessão de {formatDateTime(entry.session_datetime)}
      </p>

      {diagnosisChanged && (
        <p className="mt-2 text-xs text-slate-600">
          Diagnóstico / hipótese: <del className="text-red-800">{entry.older.diagnosis || "(vazio)"}</del>
          {" → "}<ins className="text-emerald-800 no-underline">{entry.diagnosis || "(vazio)"}</ins>
        </p>
      )}

      {showFull || !changes ? (
        <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{entry.content}</p>
      ) : (
        <div className="mt-3 space-y-0.5 text-sm leading-relaxed" aria-label="Alterações em relação à versão anterior">
          {changes.every((line) => line.type === "same") && (
            <p className="text-xs text-slate-500">O texto não mudou (só a data ou o diagnóstico).</p>
          )}
          {changes.map((line, index) => (
            line.type === "same" ? (
              <p key={index} className="whitespace-pre-wrap px-2 text-slate-600">{line.text || " "}</p>
            ) : line.type === "removed" ? (
              <del key={index} className="block whitespace-pre-wrap rounded bg-red-50 px-2 text-red-800">
                <span aria-hidden="true">− </span>{line.text || " "}
              </del>
            ) : (
              <ins key={index} className="block whitespace-pre-wrap rounded bg-emerald-50 px-2 text-emerald-800 no-underline">
                <span aria-hidden="true">+ </span>{line.text || " "}
              </ins>
            )
          ))}
        </div>
      )}

      {entry.older && changes && (
        <Button type="button" variant="link" size="sm" className="mt-1 h-auto px-0 text-[#1E3A8A]"
          onClick={() => setShowFull((value) => !value)}>
          {showFull ? "Ver só as alterações" : "Ver texto completo desta versão"}
        </Button>
      )}
    </li>
  );
}
