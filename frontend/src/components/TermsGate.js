import { useState } from "react";
import { toast } from "sonner";
import { Loader2, ShieldCheck } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PolicySections, usePrivacyPolicy, useTermsOfUse } from "@/components/PrivacyPolicyContent";

// CORREÇÃO DE CONFORMIDADE (item 16 da auditoria): tela de aceite explícito.
//
// Aparece no lugar do sistema (ver ProtectedRoute em App.js) quando o usuário
// ainda não aceitou a versão VIGENTE dos documentos legais:
//   - conta criada pelo login com Google (antes o aceite era automático);
//   - contas antigas, que "aceitaram" um documento que não existia;
//   - todos os usuários, quando os documentos mudam de versão.
// O aceite envia a versão lida; o backend registra versão, data e IP.
// Análise da administração (AD1): os Termos de Uso entraram junto. Os dois
// documentos têm a MESMA versão; cada um tem a sua aba, e um aceite cobre os dois.
export default function TermsGate() {
  const { user, setUser, logout } = useAuth();
  const privacy = usePrivacyPolicy();
  const terms = useTermsOfUse();
  const [agreed, setAgreed] = useState(false);
  const [saving, setSaving] = useState(false);

  // Quem já aceitou uma versão anterior vê a mensagem de "documentos atualizados".
  const isUpdate = Boolean(user?.terms_version);
  const ready = Boolean(privacy.policy && terms.policy);

  const accept = async () => {
    setSaving(true);
    try {
      const { data } = await api.post("/auth/accept-terms", { version: privacy.policy.version });
      setUser(data);
      toast.success("Termos de Uso e Política de Privacidade aceitos.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
      // 409 = os documentos mudaram enquanto a tela estava aberta: carrega a nova
      // versão e exige nova leitura/marcação.
      if (err.response?.status === 409) {
        setAgreed(false);
        privacy.reload();
        terms.reload();
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    // <main>: a tela de aceite é o conteúdo principal enquanto estiver aberta.
    <main className="flex min-h-screen items-center justify-center bg-[#F8FAFC] px-4 py-8">
      <Card className="w-full max-w-2xl border-slate-200 p-6 shadow-sm sm:p-8">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[#F0F4F8] text-[#1E3A8A]">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="font-head text-xl font-bold text-[#0F172A]">Termos de Uso e Política de Privacidade</h1>
            <p className="text-sm text-slate-500">
              {isUpdate
                ? "Os documentos foram atualizados. Leia e aceite a nova versão para continuar."
                : "Antes de começar, leia e aceite os Termos de Uso e a Política de Privacidade do PsiGestão."}
            </p>
          </div>
        </div>

        {/* Abas (Radix): setas do teclado trocam de documento; cada aba tem a sua rolagem. */}
        <Tabs defaultValue="terms" className="mt-5">
          {/* Cores das abas como em Configurações: o cinza padrão do componente na aba
              inativa não tem contraste suficiente (WCAG 1.4.3). */}
          <TabsList className="grid h-auto w-full grid-cols-2 bg-slate-100 text-slate-600">
            <TabsTrigger value="terms" className="whitespace-normal data-[state=active]:text-[#1E3A8A]"
              data-testid="terms-gate-tab-terms">Termos de Uso</TabsTrigger>
            <TabsTrigger value="privacy" className="whitespace-normal data-[state=active]:text-[#1E3A8A]"
              data-testid="terms-gate-tab-privacy">Política de Privacidade</TabsTrigger>
          </TabsList>
          <TabsContent value="terms">
            <div className="psi-scroll max-h-[45vh] overflow-y-auto rounded-md border border-slate-200 p-4" tabIndex={0}
              aria-label="Texto dos Termos de Uso">
              <PolicySections policy={terms.policy} error={terms.error} documentName="os Termos de Uso" idPrefix="terms" />
            </div>
          </TabsContent>
          <TabsContent value="privacy">
            <div className="psi-scroll max-h-[45vh] overflow-y-auto rounded-md border border-slate-200 p-4" tabIndex={0}
              aria-label="Texto da Política de Privacidade">
              <PolicySections policy={privacy.policy} error={privacy.error} />
            </div>
          </TabsContent>
        </Tabs>

        <div className="mt-5 flex items-start gap-2.5 rounded-md bg-[#F0F4F8] p-3">
          <Checkbox id="accept-policy" checked={agreed} onCheckedChange={(v) => setAgreed(v === true)}
            disabled={!ready} className="mt-0.5" data-testid="terms-gate-checkbox" />
          <Label htmlFor="accept-policy" className="text-sm leading-relaxed text-slate-700">
            Li e aceito os Termos de Uso e a Política de Privacidade{ready ? ` (versão ${privacy.policy.version})` : ""}.
          </Label>
        </div>

        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-between">
          <Button variant="ghost" onClick={logout} className="text-slate-500">Sair</Button>
          <Button onClick={accept} disabled={!agreed || !ready || saving}
            className="bg-[#1E3A8A] hover:bg-[#0F2C59]" data-testid="terms-gate-accept">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Aceitar e continuar"}
          </Button>
        </div>
      </Card>
    </main>
  );
}
