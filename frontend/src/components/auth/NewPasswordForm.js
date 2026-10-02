import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { PASSWORD_RULE, passwordProblem } from "@/lib/validation";
import PasswordInput from "@/components/auth/PasswordInput";

// ---------------------------------------------------------------------------
// "Senha" + "Repita a senha", com a regra visível e o aviso no próprio campo.
// Usado em "Criar senha" (fim do cadastro) e "Nova senha" (link do e-mail).
// O campo oculto com o e-mail ajuda o gerenciador de senhas a guardar a senha
// nova na conta certa.
// ---------------------------------------------------------------------------
export default function NewPasswordForm({ email, submitLabel, loading, onSubmit }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const problem = password ? passwordProblem(password) : "";
  const mismatch = confirm && confirm !== password ? "As senhas não conferem." : "";
  const hint = mismatch || problem;
  const ready = password && !problem && confirm === password;

  return (
    <form className="mt-6 space-y-4" onSubmit={(e) => { e.preventDefault(); if (ready) onSubmit(password); }}>
      <input type="text" name="username" autoComplete="username" value={email || ""} readOnly
        className="sr-only" tabIndex={-1} aria-hidden="true" />
      <div>
        <Label htmlFor="new-password" className="text-slate-700">Senha</Label>
        <div className="mt-1.5">
          <PasswordInput id="new-password" required autoFocus maxLength={72} autoComplete="new-password"
            value={password} onChange={(e) => setPassword(e.target.value)}
            aria-invalid={Boolean(problem)} aria-describedby="new-password-rules" data-testid="new-password-input" />
        </div>
      </div>
      <div>
        <Label htmlFor="confirm-password" className="text-slate-700">Repita a senha</Label>
        <div className="mt-1.5">
          <PasswordInput id="confirm-password" required maxLength={72} autoComplete="new-password"
            value={confirm} onChange={(e) => setConfirm(e.target.value)}
            aria-invalid={Boolean(mismatch)} aria-describedby="new-password-rules" data-testid="confirm-password-input" />
        </div>
      </div>
      <p id="new-password-rules" role={hint ? "alert" : undefined} className={`text-xs ${hint ? "text-red-600" : "text-slate-500"}`}>
        {hint || `${PASSWORD_RULE}.`}
      </p>
      <Button type="submit" disabled={!ready || loading} className="w-full bg-[#1E3A8A] hover:bg-[#0F2C59]"
        data-testid="new-password-submit">
        {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : submitLabel}
      </Button>
    </form>
  );
}
