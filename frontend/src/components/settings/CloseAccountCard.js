import { useState } from "react";
import { GoogleLogin } from "@react-oauth/google";
import { toast } from "sonner";
import { Download, Loader2, TriangleAlert } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { clearUserDrafts, forgetDraftKey } from "@/lib/drafts";
import { downloadFromApi } from "@/lib/download";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import PasswordInput from "@/components/auth/PasswordInput";

// ---------------------------------------------------------------------------
// SEGURANÇA MÉDIA (encerramento de conta, LGPD art. 18). A Política de
// Privacidade prometia apagar os dados ao encerrar a conta, mas não existia
// como encerrar. Regras (decisão de produto; detalhes em server.py, close_account):
//   - antes, a pessoa pode baixar TUDO (ZIP com o PDF de cada paciente e um
//     JSON completo), para cumprir a própria guarda dos prontuários;
//   - ao encerrar: ninguém mais entra; o que não está sob guarda é apagado na
//     hora; prontuários sob guarda (Resolução CFP nº 001/2009, 5 anos) ficam
//     criptografados, sem acesso, e são apagados sozinhos no fim do prazo;
//   - para encerrar: digitar ENCERRAR + senha (ou Google) + código do
//     aplicativo, se a verificação em duas etapas estiver ativa.
// ---------------------------------------------------------------------------
const CONFIRMATION = "ENCERRAR";

export default function CloseAccountCard() {
  const { user, setUser } = useAuth();
  const inFlight = useSingleFlight();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [credential, setCredential] = useState("");
  const [closing, setClosing] = useState(false);

  const hasPassword = Boolean(user?.has_password);
  const ready = typed.trim().toUpperCase() === CONFIRMATION && (hasPassword ? password : credential)
    && (!user?.mfa_enabled || code.trim());

  const exportAll = () => inFlight.run("export", async () => {
    try {
      await downloadFromApi("/account/export", "psigestao_exportacao.zip");
      toast.success("Exportação completa baixada. Guarde o arquivo em local protegido.");
    } catch (err) {
      toast.error(err.message);
    }
  });

  const reset = (value) => {
    setOpen(value);
    if (!value) {
      setTyped("");
      setPassword("");
      setCode("");
      setCredential("");
    }
  };

  const close = async (e) => {
    e.preventDefault();
    if (!ready) return;
    setClosing(true);
    try {
      const { data } = await api.post("/account/close", {
        confirmation: typed, password: password || null, credential: credential || null, code: code || null,
      });
      // Os rascunhos deste aparelho são de uma conta que não existe mais.
      clearUserDrafts(user.user_id);
      forgetDraftKey();
      toast.success(data.retained_patients
        ? `Conta encerrada. ${data.retained_patients === 1 ? "1 paciente com prontuário fica" : `${data.retained_patients} pacientes com prontuário ficam`} sob guarda, sem acesso de ninguém, e será excluído automaticamente no fim do prazo.`
        : "Conta encerrada. Seus dados foram excluídos.", { duration: 12000 });
      setUser(false); // a rota protegida leva ao login
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      setCode("");
    } finally {
      setClosing(false);
    }
  };

  return (
    <Card className="border-red-200 p-6 shadow-sm" data-testid="close-account-card">
      <div className="flex items-center gap-2">
        <TriangleAlert className="h-4 w-4 text-red-600" aria-hidden="true" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Encerrar conta</h2>
      </div>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-relaxed text-slate-600">
        <li>Ninguém mais consegue entrar na conta, e a agenda é apagada.</li>
        <li>Pacientes sem prontuário sob guarda são excluídos na hora.</li>
        <li>
          Prontuários dentro da guarda obrigatória (5 anos, Resolução CFP nº 001/2009) ficam criptografados, sem acesso de
          ninguém, e são excluídos automaticamente no fim do prazo. Baixe uma cópia antes: a guarda é responsabilidade
          do(a) psicólogo(a).
        </li>
      </ul>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={exportAll} disabled={inFlight.isBusy("export")}
          className="gap-2 border-slate-300" data-testid="export-account-btn">
          {inFlight.isBusy("export") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" aria-hidden="true" />}
          Baixar todos os meus dados (ZIP)
        </Button>
        <Button type="button" variant="outline" onClick={() => reset(true)}
          className="border-red-300 text-red-700 hover:bg-red-50" data-testid="close-account-btn">
          Encerrar minha conta
        </Button>
      </div>

      <Dialog open={open} onOpenChange={reset}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="font-head">Encerrar a conta definitivamente</DialogTitle>
            <DialogDescription>
              Esta ação não pode ser desfeita. Baixou a cópia dos seus dados? Se não, cancele e use
              “Baixar todos os meus dados” antes.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={close} className="space-y-4">
            <div>
              <Label htmlFor="close-confirmation" className="text-slate-700">Digite {CONFIRMATION} para confirmar</Label>
              <Input id="close-confirmation" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off"
                className="mt-1.5" data-testid="close-confirmation-input" />
            </div>
            {hasPassword ? (
              <div>
                <Label htmlFor="close-password" className="text-slate-700">Senha atual</Label>
                <div className="mt-1.5">
                  <PasswordInput id="close-password" autoComplete="current-password" value={password}
                    onChange={(e) => setPassword(e.target.value)} data-testid="close-password-input" />
                </div>
              </div>
            ) : (
              <div>
                <p className="text-sm text-slate-700">Confirme que é você com a sua conta Google:</p>
                {credential ? (
                  <p className="mt-2 text-sm font-medium text-emerald-700" role="status">Identidade confirmada pelo Google.</p>
                ) : (
                  <div className="mt-2">
                    <GoogleLogin onSuccess={(response) => setCredential(response.credential)} text="continue_with"
                      shape="rectangular" onError={() => toast.error("Não foi possível confirmar com o Google.")} />
                  </div>
                )}
              </div>
            )}
            {user?.mfa_enabled && (
              <div>
                <Label htmlFor="close-code" className="text-slate-700">Código do aplicativo autenticador</Label>
                <Input id="close-code" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code"
                  inputMode="numeric" maxLength={32} className="mt-1.5 font-mono tracking-widest" />
              </div>
            )}
            <DialogFooter className="gap-2">
              <Button type="button" variant="outline" onClick={() => reset(false)}>Cancelar</Button>
              <Button type="submit" disabled={!ready || closing} className="bg-red-600 hover:bg-red-700"
                data-testid="close-account-submit">
                {closing ? <Loader2 className="h-4 w-4 animate-spin" /> : "Encerrar definitivamente"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
