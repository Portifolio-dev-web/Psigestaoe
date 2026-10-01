import { useState } from "react";
import { toast } from "sonner";
import { Loader2, ShieldCheck } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { PolicySections, usePrivacyPolicy } from "@/components/PrivacyPolicyContent";

// CORREÇÃO DE CONFORMIDADE (item 16 da auditoria): tela de aceite explícito.
//
// Aparece no lugar do sistema (ver ProtectedRoute em App.js) quando o usuário
// ainda não aceitou a versão VIGENTE da Política de Privacidade:
//   - conta criada pelo login com Google (antes o aceite era automático);
//   - contas antigas, que "aceitaram" um documento que não existia;
//   - todos os usuários, quando a política muda de versão.
// O aceite envia a versão lida; o backend registra versão, data e IP.
export default function TermsGate() {
  const { user, setUser, logout } = useAuth();
  const { policy, error, reload } = usePrivacyPolicy();
  const [agreed, setAgreed] = useState(false);
  const [saving, setSaving] = useState(false);

  // Quem já aceitou uma versão anterior vê a mensagem de "política atualizada".
  const isUpdate = Boolean(user?.terms_version);

  const accept = async () => {
    setSaving(true);
    try {
      const { data } = await api.post("/auth/accept-terms", { version: policy.version });
      setUser(data);
      toast.success("Política de Privacidade aceita.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
      // 409 = a política mudou enquanto a tela estava aberta: carrega a nova
      // versão e exige nova leitura/marcação.
      if (err.response?.status === 409) {
        setAgreed(false);
        reload();
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F8FAFC] px-4 py-8">
      <Card className="w-full max-w-2xl border-slate-200 p-6 shadow-sm sm:p-8">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[#F0F4F8] text-[#1E3A8A]">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h1 className="font-head text-xl font-bold text-[#0F172A]">Política de Privacidade</h1>
            <p className="text-sm text-slate-500">
              {isUpdate
                ? "A Política de Privacidade foi atualizada. Leia e aceite a nova versão para continuar."
                : "Antes de começar, leia e aceite a Política de Privacidade do PsiGestão."}
            </p>
          </div>
        </div>

        <div className="psi-scroll mt-5 max-h-[50vh] overflow-y-auto rounded-md border border-slate-200 p-4" tabIndex={0}
          aria-label="Texto da Política de Privacidade">
          <PolicySections policy={policy} error={error} />
        </div>

        <div className="mt-5 flex items-start gap-2.5 rounded-md bg-[#F0F4F8] p-3">
          <Checkbox id="accept-policy" checked={agreed} onCheckedChange={(v) => setAgreed(v === true)}
            disabled={!policy} className="mt-0.5" data-testid="terms-gate-checkbox" />
          <Label htmlFor="accept-policy" className="text-sm leading-relaxed text-slate-700">
            Li e aceito a Política de Privacidade{policy ? ` (versão ${policy.version})` : ""}.
          </Label>
        </div>

        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-between">
          <Button variant="ghost" onClick={logout} className="text-slate-500">Sair</Button>
          <Button onClick={accept} disabled={!agreed || !policy || saving}
            className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="terms-gate-accept">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Aceitar e continuar"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
