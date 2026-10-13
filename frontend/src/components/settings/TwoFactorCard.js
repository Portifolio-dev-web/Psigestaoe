import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { Copy, Download, Loader2, ShieldCheck, ShieldAlert, Smartphone } from "lucide-react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { downloadText } from "@/lib/download";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import PasswordInput from "@/components/auth/PasswordInput";

// ---------------------------------------------------------------------------
// SEGURANÇA (backlog: MFA): verificação em duas etapas, em Configurações >
// Minha conta. Opcional (decisão de produto), com convite no painel.
//
// Ativar: 1) o servidor cria o segredo (POST /auth/mfa/setup) e a tela mostra
// o QR code (e a chave, para digitar à mão); 2) a pessoa confirma com o
// primeiro código do aplicativo (POST /auth/mfa/enable); 3) a tela mostra os
// 10 códigos de recuperação UMA vez (o servidor guarda só o hash).
// Desativar pede a senha E um código: quem achar o computador logado não
// consegue desligar a proteção.
// Quem entra só pelo Google usa a verificação da própria conta Google.
// ---------------------------------------------------------------------------

// "JBSWY3DPEHPK3PXP..." -> "JBSW Y3DP EHPK ..." (mais fácil de digitar no celular).
const groupSecret = (secret) => secret.replace(/(.{4})/g, "$1 ").trim();

function CodeInput({ id, value, onChange, recovery = false, ...props }) {
  return (
    <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} required
      autoComplete="one-time-code" inputMode={recovery ? "text" : "numeric"} maxLength={recovery ? 32 : 6}
      pattern={recovery ? undefined : "[0-9]{6}"} placeholder={recovery ? "código do app ou de recuperação" : "000000"}
      className="mt-1.5 font-mono tracking-widest" {...props} />
  );
}

function RecoveryCodes({ codes, onDone }) {
  const [saved, setSaved] = useState(false);
  const text = `PsiGestão: códigos de recuperação da verificação em duas etapas\n`
    + `Cada código vale UMA vez. Guarde em local seguro (fora deste computador).\n\n${codes.join("\n")}\n`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Códigos copiados.");
    } catch {
      toast.error("Não foi possível copiar. Use “Baixar”.");
    }
  };
  return (
    <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-4" data-testid="mfa-recovery-codes">
      <p className="text-sm font-medium text-amber-900">Guarde estes códigos de recuperação</p>
      <p className="mt-1 text-xs leading-relaxed text-amber-900">
        Se perder o celular, cada código permite entrar uma vez. Eles aparecem só agora: copie, baixe ou anote e
        guarde fora deste computador (ex.: no gerenciador de senhas).
      </p>
      <ul className="mt-3 grid grid-cols-1 gap-1.5 font-mono text-sm text-[#0F172A] sm:grid-cols-2" aria-label="Códigos de recuperação">
        {codes.map((code) => <li key={code} className="rounded bg-white px-2 py-1">{code}</li>)}
      </ul>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" className="gap-1.5 border-amber-300 bg-white" onClick={copy}>
          <Copy className="h-4 w-4" aria-hidden="true" /> Copiar
        </Button>
        <Button type="button" size="sm" variant="outline" className="gap-1.5 border-amber-300 bg-white"
          onClick={() => downloadText(text, "psigestao-codigos-de-recuperacao.txt")}>
          <Download className="h-4 w-4" aria-hidden="true" /> Baixar .txt
        </Button>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Checkbox id="mfa-codes-saved" checked={saved} onCheckedChange={(value) => setSaved(Boolean(value))} />
        <Label htmlFor="mfa-codes-saved" className="text-sm text-amber-900">Guardei os códigos em local seguro</Label>
      </div>
      <Button type="button" size="sm" disabled={!saved} onClick={onDone} className="mt-3 bg-[#1E3A8A] hover:bg-[#0F2C59]"
        data-testid="mfa-codes-done">
        Concluir
      </Button>
    </div>
  );
}

// `allowDisable`: na conta suspensa (modo restrito) o servidor não deixa desativar
// as duas etapas (revisão de segurança, RS1); o botão nem aparece.
export default function TwoFactorCard({ allowDisable = true }) {
  const { user, setUser } = useAuth();
  const [setup, setSetup] = useState(null); // { secret, otpauth_uri } durante a ativação
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState(null); // códigos de recuperação recém-gerados
  const [dialog, setDialog] = useState(null); // "disable" | "regenerate"
  const [password, setPassword] = useState("");

  const enabled = Boolean(user?.mfa_enabled);
  const left = user?.mfa_recovery_codes_left ?? 0;

  const call = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || err.message);
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  const start = () => call(async () => {
    const { data } = await api.post("/auth/mfa/setup");
    setSetup(data);
    setCode("");
  });

  const confirm = (e) => {
    e.preventDefault();
    call(async () => {
      const { data } = await api.post("/auth/mfa/enable", { code });
      setUser((current) => ({ ...current, ...data.user }));
      setSetup(null);
      setCode("");
      setCodes(data.recovery_codes);
      toast.success("Verificação em duas etapas ativada.");
    });
  };

  const closeDialog = () => {
    setDialog(null);
    setCode("");
    setPassword("");
  };

  const submitDialog = (e) => {
    e.preventDefault();
    call(async () => {
      if (dialog === "disable") {
        const { data } = await api.post("/auth/mfa/disable", { password, code });
        setUser((current) => ({ ...current, ...data.user }));
        toast.success("Verificação em duas etapas desativada.");
      } else {
        const { data } = await api.post("/auth/mfa/recovery-codes", { code });
        setUser((current) => ({ ...current, ...data.user }));
        setCodes(data.recovery_codes);
        toast.success("Novos códigos gerados. Os anteriores deixaram de valer.");
      }
      closeDialog();
    });
  };

  return (
    <Card className="border-slate-200 p-6 shadow-sm" data-testid="mfa-card">
      <div className="flex flex-wrap items-center gap-2">
        <ShieldCheck className="h-4 w-4 text-[#1E3A8A]" aria-hidden="true" />
        <h2 className="font-head text-lg font-bold text-[#0F172A]">Verificação em duas etapas</h2>
        {enabled
          ? <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">Ativa</Badge>
          : <Badge variant="outline" className="border-slate-300 text-slate-600">Desativada</Badge>}
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Além da senha, o login pede um código de 6 dígitos do aplicativo autenticador do seu celular (Google
        Authenticator, Microsoft Authenticator, 1Password...). Uma senha vazada, sozinha, não abre os prontuários.
      </p>

      {codes ? (
        <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />
      ) : enabled ? (
        <div className="mt-4 space-y-3">
          <p className={`flex items-center gap-1.5 text-sm ${left <= 3 ? "text-amber-800" : "text-slate-600"}`}>
            {left <= 3 && <ShieldAlert className="h-4 w-4" aria-hidden="true" />}
            {left === 1 ? "Resta 1 código de recuperação." : `Restam ${left} códigos de recuperação.`}
            {left <= 3 && " Gere novos códigos."}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" className="border-slate-300" onClick={() => setDialog("regenerate")}>
              Gerar novos códigos de recuperação
            </Button>
            {allowDisable && (
              <Button type="button" variant="outline" size="sm" className="border-red-200 text-red-600 hover:bg-red-50"
                onClick={() => setDialog("disable")} data-testid="mfa-disable-btn">
                Desativar
              </Button>
            )}
          </div>
        </div>
      ) : !user?.has_password ? (
        <p className="mt-4 rounded-md bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-600">
          Você entra com o Google: a verificação em duas etapas é a da sua conta Google (ative em
          myaccount.google.com &gt; Segurança). Para usá-la também aqui, defina antes uma senha em “Senha e acesso”.
        </p>
      ) : setup ? (
        <form onSubmit={confirm} className="mt-4 grid grid-cols-1 gap-5 md:grid-cols-[auto_1fr]" data-testid="mfa-setup">
          <div className="flex justify-center">
            {/* Branco ao redor: alguns leitores de QR não leem o código encostado na borda. */}
            <div className="rounded-lg border border-slate-200 bg-white p-3">
              <QRCodeSVG value={setup.otpauth_uri} size={168} level="M" title="QR code para o aplicativo autenticador" />
            </div>
          </div>
          <div className="space-y-3">
            <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-600">
              <li>No aplicativo autenticador, toque em “Adicionar” e leia o QR code.</li>
              <li>Sem câmera? Digite a chave abaixo.</li>
              <li>Confirme com o código de 6 dígitos que aparecer.</li>
            </ol>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Chave</p>
              <div className="mt-1 flex items-center gap-2">
                {/* break-words: quebra entre os grupos de 4, nunca no meio de um. */}
                <code className="break-words rounded bg-slate-100 px-2 py-1 text-sm text-[#0F172A]" data-testid="mfa-secret">
                  {groupSecret(setup.secret)}
                </code>
                <Button type="button" variant="ghost" size="icon" aria-label="Copiar a chave"
                  onClick={() => navigator.clipboard?.writeText(setup.secret).then(() => toast.success("Chave copiada."))}>
                  <Copy className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            </div>
            <div className="max-w-[220px]">
              <Label htmlFor="mfa-enable-code" className="text-slate-700">Código do aplicativo</Label>
              <CodeInput id="mfa-enable-code" value={code} onChange={setCode} autoFocus data-testid="mfa-enable-code" />
            </div>
            <div className="flex gap-2">
              <Button type="submit" disabled={busy || code.length !== 6} className="bg-[#1E3A8A] hover:bg-[#0F2C59]"
                data-testid="mfa-enable-submit">
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Confirmar e ativar"}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setSetup(null)}>Cancelar</Button>
            </div>
          </div>
        </form>
      ) : (
        <Button type="button" onClick={start} disabled={busy} className="mt-4 gap-2 bg-[#1E3A8A] hover:bg-[#0F2C59]"
          data-testid="mfa-start-btn">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Smartphone className="h-4 w-4" aria-hidden="true" />}
          Ativar verificação em duas etapas
        </Button>
      )}

      <Dialog open={Boolean(dialog)} onOpenChange={(open) => { if (!open) closeDialog(); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="font-head">
              {dialog === "disable" ? "Desativar a verificação em duas etapas" : "Gerar novos códigos de recuperação"}
            </DialogTitle>
            <DialogDescription>
              {dialog === "disable"
                ? "Confirme com a senha e um código do aplicativo. Depois disso, o login volta a pedir só a senha."
                : "Confirme com um código do aplicativo. Os códigos de recuperação anteriores deixam de valer."}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={submitDialog} className="space-y-4">
            {dialog === "disable" && (
              <div>
                <Label htmlFor="mfa-disable-password" className="text-slate-700">Senha atual</Label>
                <div className="mt-1.5">
                  <PasswordInput id="mfa-disable-password" required autoComplete="current-password" value={password}
                    onChange={(e) => setPassword(e.target.value)} />
                </div>
              </div>
            )}
            <div>
              <Label htmlFor="mfa-dialog-code" className="text-slate-700">
                {dialog === "disable" ? "Código do aplicativo (ou de recuperação)" : "Código do aplicativo"}
              </Label>
              <CodeInput id="mfa-dialog-code" value={code} onChange={setCode} recovery={dialog === "disable"} />
            </div>
            <DialogFooter className="gap-2">
              <Button type="button" variant="outline" onClick={closeDialog}>Cancelar</Button>
              <Button type="submit" disabled={busy}
                className={dialog === "disable" ? "bg-red-600 hover:bg-red-700" : "bg-[#1E3A8A] hover:bg-[#0F2C59]"}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : dialog === "disable" ? "Desativar" : "Gerar códigos"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
