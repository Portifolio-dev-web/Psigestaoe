import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Laptop, Loader2, LogOut, MonitorSmartphone, Smartphone } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { formatDateTime } from "@/lib/datetime";
import { countLabel } from "@/lib/utils";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { useConfirm } from "@/hooks/useConfirm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

// ---------------------------------------------------------------------------
// SEGURANÇA MÉDIA (sessões com aparelho): antes esta área dizia só QUANTAS
// outras sessões estavam abertas ("2 outras sessões"), sem como saber se uma
// delas era um acesso estranho nem encerrar só a do computador da clínica.
// Agora cada sessão aparece com aparelho, IP, quando entrou e o último uso, e
// pode ser encerrada sozinha. "Sair dos outros dispositivos" continua aqui.
// ---------------------------------------------------------------------------

const deviceIcon = (device) => (/Android|iPhone/.test(device) ? Smartphone : /iPad/.test(device) ? MonitorSmartphone : Laptop);

export default function ConnectedDevicesCard() {
  const [sessions, setSessions] = useState(null);
  const inFlight = useSingleFlight();
  // Análise de UI (F2): confirmação no padrão do sistema (antes window.confirm).
  const [confirm, confirmDialog] = useConfirm();

  const load = useCallback(() => {
    api.get("/auth/sessions")
      .then(({ data }) => setSessions(data.sessions))
      .catch(() => setSessions([]));
  }, []);

  useEffect(() => { load(); }, [load]);

  const endOne = (session) => inFlight.run(`end:${session.id}`, async () => {
    try {
      // Corpo JSON obrigatório: o servidor recusa a rota sem ele (proteção contra CSRF).
      await api.post(`/auth/sessions/${session.id}/end`, { confirm: true });
      toast.success(`Sessão encerrada (${session.device}).`);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
    load();
  });

  const endOthers = () => inFlight.run("end-others", async () => {
    if (!(await confirm({
      title: "Sair de todos os outros aparelhos?",
      description: "A sessão é encerrada em todos os outros aparelhos e navegadores. Este continua conectado.",
      confirmLabel: "Sair dos outros aparelhos",
      destructive: true,
    }))) return;
    try {
      const { data } = await api.post("/auth/sessions/end-others", { confirm: true });
      toast.success(data.ended_sessions
        ? `${countLabel(data.ended_sessions, "sessão encerrada em outro aparelho", "sessões encerradas em outros aparelhos")}.`
        : "Não havia outras sessões abertas.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    }
    load();
  });

  const others = (sessions || []).filter((s) => !s.current).length;

  return (
    <Card className="border-slate-200 p-6 shadow-sm" data-testid="devices-card">
      <div className="flex items-center gap-2">
        <MonitorSmartphone className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Aparelhos conectados</h2>
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Onde sua conta está aberta agora. Não reconhece algum? Encerre a sessão e troque a senha.
      </p>

      {sessions === null ? (
        <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-[#1E3A8A]" /></div>
      ) : (
        <ul className="mt-4 divide-y divide-slate-100" data-testid="devices-list">
          {sessions.map((s) => {
            const Icon = deviceIcon(s.device);
            return (
              <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#F0F4F8] text-[#1E3A8A]">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-[#0F172A]">
                    {s.device}
                    {s.current && <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">Este aparelho</Badge>}
                  </p>
                  <p className="text-xs text-slate-500">
                    {s.ip_address ? `IP ${s.ip_address} · ` : ""}entrou em {formatDateTime(s.created_at)}
                    {!s.current && ` · último uso ${formatDateTime(s.last_activity_at)}`}
                  </p>
                </div>
                {!s.current && (
                  <Button type="button" variant="outline" size="sm" disabled={inFlight.isBusy(`end:${s.id}`)}
                    onClick={() => endOne(s)} className="border-slate-300"
                    aria-label={`Encerrar a sessão de ${s.device}`} data-testid={`end-session-${s.id}`}>
                    {inFlight.isBusy(`end:${s.id}`) ? <Loader2 className="h-4 w-4 animate-spin" /> : "Encerrar"}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <Button variant="outline" size="sm" onClick={endOthers} disabled={inFlight.isBusy("end-others") || !others}
        className="mt-3 gap-2 border-red-200 text-red-600 hover:bg-red-50" data-testid="end-other-sessions-btn">
        {inFlight.isBusy("end-others") ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" aria-hidden="true" />}
        Sair de todos os outros aparelhos
      </Button>
      {confirmDialog}
    </Card>
  );
}
