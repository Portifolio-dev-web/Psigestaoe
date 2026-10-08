import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Check, Copy, Link2, Loader2, MessageCircle } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useConfirm } from "@/hooks/useConfirm";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { useWriteAccess } from "@/context/WriteAccessContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// PRODUTO: link de cadastro do paciente (substitui a integração com o Google Forms).
//
// A psicóloga gera um link e manda ao paciente (WhatsApp, e-mail...). O paciente
// preenche a própria ficha no celular, com as mesmas regras do cadastro manual.
//   - o link aparece UMA vez (o servidor guarda só o hash): perdeu, gera outro;
//   - vale 30 minutos para começar; começou, 10 minutos para terminar (o
//     paciente pode pedir mais tempo);
//   - a lista mostra cada link e o que aconteceu com ele. Se o tempo acabou
//     pela metade, o que o paciente digitou chega como "incompleto": a
//     psicóloga revisa e completa (onReviewIncomplete abre a ficha preenchida).
// A lista se atualiza sozinha a cada 15 s enquanto a janela está aberta.
// ---------------------------------------------------------------------------
const REFRESH_MS = 15000;
const time = (iso) => (iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "");
const dateTime = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "");

// Situação de cada link, em texto (a cor só reforça; WCAG 1.4.1).
const STATUS = {
  pending: { label: "Aguardando o paciente", cls: "bg-blue-50 text-[#1E3A8A]", detail: (i) => `começa até ${time(i.expires_at)}` },
  in_progress: { label: "Paciente preenchendo", cls: "bg-amber-50 text-amber-900", detail: (i) => `até ${time(i.deadline_at)}` },
  submitted: { label: "Recebido", cls: "bg-emerald-50 text-emerald-800", detail: (i) => dateTime(i.submitted_at) },
  completed: { label: "Completado por você", cls: "bg-emerald-50 text-emerald-800", detail: (i) => dateTime(i.submitted_at) },
  incomplete: { label: "Incompleto", cls: "bg-red-50 text-red-800", detail: (i) => `o tempo acabou; salvo às ${time(i.draft_saved_at)}` },
  expired: { label: "Venceu sem uso", cls: "bg-slate-100 text-slate-700", detail: (i) => dateTime(i.expires_at) },
  revoked: { label: "Cancelado", cls: "bg-slate-100 text-slate-700", detail: () => "" },
};

export default function IntakeLinksDialog({ open, onOpenChange, onReviewIncomplete, refreshKey }) {
  const [label, setLabel] = useState("");
  const [created, setCreated] = useState(null); // { url, expires_at } do link recém-gerado
  const [copied, setCopied] = useState(false);
  const [links, setLinks] = useState(null);
  const inFlight = useSingleFlight();
  const [confirm, confirmDialog] = useConfirm();
  // Produto (assinatura): gerar link é cadastrar paciente novo: no somente leitura, explica.
  const { guard } = useWriteAccess();

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/intake-links");
      setLinks(data);
    } catch {
      setLinks((current) => current || []);
    }
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [open, load, refreshKey]);

  // Fechou a janela: o link gerado some da tela (não fica guardado em lugar nenhum).
  useEffect(() => {
    if (!open) {
      setCreated(null);
      setCopied(false);
      setLabel("");
    }
  }, [open]);

  const create = guard(() => inFlight.run("create", async () => {
    try {
      const { data } = await api.post("/intake-links", { label: label.trim() });
      setCreated(data);
      setCopied(false);
      setLabel("");
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  }));

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.url);
      setCopied(true);
      toast.success("Link copiado.");
    } catch {
      // Sem permissão de área de transferência: seleciona o texto para copiar à mão.
      document.getElementById("intake-link-url")?.select();
      toast.info("Selecione e copie o link no campo acima.");
    }
  };

  const revoke = (intake) => inFlight.run(`revoke:${intake.id}`, async () => {
    const ok = await confirm({
      title: "Cancelar este link?",
      description: intake.status === "in_progress"
        ? "O paciente está preenchendo agora. Cancelando, o link para de funcionar e o que ele já digitou é apagado."
        : "O link para de funcionar. Se precisar, gere outro.",
      confirmLabel: "Cancelar link",
      cancelLabel: "Manter",
      destructive: true,
    });
    if (!ok) return;
    try {
      await api.post(`/intake-links/${intake.id}/revoke`, { confirm: true });
      toast.success("Link cancelado.");
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  const discard = (intake) => inFlight.run(`discard:${intake.id}`, async () => {
    const ok = await confirm({
      title: "Descartar o cadastro incompleto?",
      description: "O que o paciente preencheu será apagado. Use quando ele desistiu ou quando você vai cadastrá-lo de outra forma.",
      confirmLabel: "Descartar",
      cancelLabel: "Manter",
      destructive: true,
    });
    if (!ok) return;
    try {
      await api.post(`/intake-links/${intake.id}/discard`, { confirm: true });
      toast.success("Cadastro incompleto descartado.");
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
  });

  const whatsappText = created
    ? `Olá! Este é o link para você preencher seu cadastro com segurança. Ele vale por 30 minutos para começar; `
      + `depois de começar, são 10 minutos para terminar: ${created.url}`
    : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-xl" data-testid="intake-links-dialog">
        <DialogHeader>
          <DialogTitle className="font-head">Link de cadastro do paciente</DialogTitle>
          <DialogDescription>
            O paciente preenche a própria ficha pelo celular. O link vale 30 minutos para começar; depois de começar,
            são 10 minutos para terminar (ele pode pedir mais tempo).
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={(e) => { e.preventDefault(); create(); }} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1">
            <Label htmlFor="intake-label">Para quem é? <span className="font-normal text-slate-500">(opcional)</span></Label>
            <Input id="intake-label" value={label} maxLength={60} onChange={(e) => setLabel(e.target.value)}
              placeholder="Ex.: Ana, primeira consulta" aria-describedby="intake-label-hint" className="mt-1.5"
              data-testid="intake-label-input" />
            <p id="intake-label-hint" className="mt-1 text-xs text-slate-500">Só você vê, para reconhecer o link na lista.</p>
          </div>
          <Button type="submit" disabled={inFlight.isBusy("create")} className="gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59] sm:mb-5"
            data-testid="intake-create-btn">
            {inFlight.isBusy("create") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" aria-hidden="true" />}
            Gerar link
          </Button>
        </form>

        {created && (
          <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50 p-4" role="status" data-testid="intake-created">
            <Label htmlFor="intake-link-url" className="text-[#0F2C59]">Link gerado (vale para começar até {time(created.expires_at)})</Label>
            <Input id="intake-link-url" readOnly value={created.url} onFocus={(e) => e.target.select()} className="bg-white text-xs"
              data-testid="intake-link-url" />
            <div className="flex flex-wrap gap-2">
              <Button type="button" onClick={copy} variant="outline" className="gap-2 border-slate-300 bg-white" data-testid="intake-copy">
                {copied ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
                {copied ? "Copiado" : "Copiar link"}
              </Button>
              {/* Verde-escuro do WhatsApp: o verde claro com texto branco não tem contraste (WCAG 1.4.3). */}
              <a href={`https://wa.me/?text=${encodeURIComponent(whatsappText)}`} target="_blank" rel="noopener noreferrer"
                className="inline-flex h-10 items-center gap-2 rounded-md bg-[#075E54] px-4 text-sm font-medium text-white hover:bg-[#054C44] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A] focus-visible:ring-offset-2"
                data-testid="intake-whatsapp">
                <MessageCircle className="h-4 w-4" aria-hidden="true" /> Enviar pelo WhatsApp
              </a>
            </div>
            <p className="text-xs text-[#0F2C59]">
              O link aparece só agora (por segurança, o sistema não guarda uma cópia). Se perder, gere outro.
              Mande somente para o paciente: quem tiver o link pode preencher.
            </p>
          </div>
        )}

        <section aria-labelledby="intake-list-title" className="space-y-2">
          <h3 id="intake-list-title" className="font-head text-sm font-bold text-[#0F172A]">Links recentes</h3>
          {links === null ? (
            <div className="flex justify-center py-4"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" aria-label="Carregando" /></div>
          ) : links.length === 0 ? (
            <p className="text-sm text-slate-500">Nenhum link gerado ainda.</p>
          ) : (
            <ul className="divide-y divide-slate-100 rounded-md border border-slate-200" data-testid="intake-list">
              {links.map((intake) => {
                const status = STATUS[intake.status] || STATUS.expired;
                return (
                  <li key={intake.id} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
                    data-testid={`intake-${intake.id}`} data-status={intake.status}>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-[#0F172A]">{intake.label || "Sem identificação"}</p>
                      <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-600">
                        <span className={`rounded px-1.5 py-0.5 font-medium ${status.cls}`}>{status.label}</span>
                        <span>{status.detail(intake)}</span>
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {intake.status === "incomplete" && (
                        <>
                          <Button type="button" size="sm" onClick={() => onReviewIncomplete(intake)}
                            className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid={`intake-review-${intake.id}`}>
                            Revisar e completar
                          </Button>
                          <Button type="button" size="sm" variant="ghost" onClick={() => discard(intake)}
                            disabled={inFlight.isBusy(`discard:${intake.id}`)} className="text-red-700">
                            Descartar
                          </Button>
                        </>
                      )}
                      {["pending", "in_progress"].includes(intake.status) && (
                        <Button type="button" size="sm" variant="ghost" onClick={() => revoke(intake)}
                          disabled={inFlight.isBusy(`revoke:${intake.id}`)} className="text-red-700"
                          data-testid={`intake-revoke-${intake.id}`}>
                          Cancelar link
                        </Button>
                      )}
                      {intake.patient_id && (
                        <Link to={`/pacientes/${intake.patient_id}`} onClick={() => onOpenChange(false)}
                          className="inline-flex min-h-8 items-center rounded-md px-2 text-sm font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]">
                          Abrir ficha
                        </Link>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
        {confirmDialog}
      </DialogContent>
    </Dialog>
  );
}
