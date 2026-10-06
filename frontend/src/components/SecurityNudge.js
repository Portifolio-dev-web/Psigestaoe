import { useState } from "react";
import { Link } from "react-router-dom";
import { ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";

// ---------------------------------------------------------------------------
// SEGURANÇA (backlog: MFA opcional, "com convite"): faixa no painel.
//   - quem entra com SENHA e não ativou a verificação em duas etapas recebe
//     o convite (com link direto para Configurações > Minha conta);
//   - quem ativou e está com 2 ou menos códigos de recuperação recebe o aviso
//     para gerar novos (sem código e sem celular, ninguém entra).
// "Agora não" esconde o convite NESTE aparelho por 30 dias (localStorage é só
// conveniência: se não houver, o convite simplesmente volta a aparecer).
// Quem entra só pelo Google não vê o convite: usa a verificação da conta Google.
// Análise de UI (O1): `hideInvite` esconde o convite enquanto a lista
// "Primeiros passos" do painel está ativa (ativar as duas etapas é um dos
// passos; dois convites iguais na mesma tela seriam ruído).
// ---------------------------------------------------------------------------
const SNOOZE_DAYS = 30;
const snoozeKey = (userId) => `psi:mfa-nudge:${userId}`;

function snoozed(userId) {
  try {
    const until = Number(window.localStorage.getItem(snoozeKey(userId)) || 0);
    return until > Date.now();
  } catch {
    return false;
  }
}

export default function SecurityNudge({ hideInvite = false }) {
  const { user } = useAuth();
  const [hidden, setHidden] = useState(() => (user ? snoozed(user.user_id) : true));
  if (!user) return null;

  const lowCodes = user.mfa_enabled && user.mfa_recovery_codes_left <= 2;
  const invite = user.has_password && !user.mfa_enabled && !hidden && !hideInvite;
  if (!lowCodes && !invite) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(snoozeKey(user.user_id), String(Date.now() + SNOOZE_DAYS * 86400000));
    } catch {
      // sem armazenamento local: esconde só até recarregar a página
    }
    setHidden(true);
  };

  if (lowCodes) {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3" role="status"
        data-testid="security-nudge">
        <ShieldAlert className="h-5 w-5 shrink-0 text-amber-700" aria-hidden="true" />
        <p className="min-w-0 flex-1 text-sm text-amber-900">
          {user.mfa_recovery_codes_left === 1
            ? "Resta 1 código de recuperação"
            : user.mfa_recovery_codes_left === 0 ? "Acabaram os códigos de recuperação" : `Restam ${user.mfa_recovery_codes_left} códigos de recuperação`}
          {" "}da verificação em duas etapas. Gere novos para não ficar sem acesso se perder o celular.
        </p>
        <Button asChild size="sm" className="bg-[#1E3A8A] hover:bg-[#0F2C59]">
          <Link to="/configuracoes?aba=conta">Gerar novos códigos</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3" role="status"
      data-testid="security-nudge">
      <ShieldCheck className="h-5 w-5 shrink-0 text-[#1E3A8A]" aria-hidden="true" />
      <p className="min-w-0 flex-1 text-sm text-[#0F2C59]">
        <b>Proteja os prontuários com a verificação em duas etapas.</b> Além da senha, o login pede um código do
        seu celular: uma senha vazada, sozinha, não abre a conta.
      </p>
      <div className="flex items-center gap-2">
        <Button asChild size="sm" className="bg-[#1E3A8A] hover:bg-[#0F2C59]">
          <Link to="/configuracoes?aba=conta" data-testid="security-nudge-activate">Ativar agora</Link>
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={dismiss} className="gap-1 text-[#1E3A8A]"
          data-testid="security-nudge-dismiss">
          <X className="h-4 w-4" aria-hidden="true" /> Agora não
        </Button>
      </div>
    </div>
  );
}
