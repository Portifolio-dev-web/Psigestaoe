import { forwardRef, useState } from "react";
import { Eye, EyeOff, Lock } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Campo de senha com botão "mostrar/ocultar" (complementares: login).
// No celular é fácil errar uma letra sem ver o que foi digitado; o botão
// deixa conferir antes de enviar. Começa sempre oculta.
// O ícone de cadeado à esquerda é opcional (`withIcon`), como no login.
// ---------------------------------------------------------------------------
const PasswordInput = forwardRef(function PasswordInput({ className, withIcon = false, ...props }, ref) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      {withIcon && <Lock className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />}
      <Input ref={ref} type={visible ? "text" : "password"} className={cn(withIcon && "pl-9", "pr-10", className)} {...props} />
      <button type="button" onClick={() => setVisible((value) => !value)}
        className="absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]"
        // Rótulo fixo + aria-pressed (ligado/desligado): o padrão para botões de alternar.
        aria-label="Mostrar senha" aria-pressed={visible}
        data-testid={props["data-testid"] ? `${props["data-testid"]}-toggle` : undefined}>
        {/* Crossfade (prévia validada): os dois ícones ficam empilhados e só a
            opacidade/escala transicionam — troca suave, não instantânea.
            motion-reduce: cai para "sem transição" (A3, mesmo padrão do app). */}
        <Eye aria-hidden="true"
          className={cn("absolute h-4 w-4 transition-all duration-150 motion-reduce:transition-none",
            visible ? "scale-75 opacity-0" : "scale-100 opacity-100")} />
        <EyeOff aria-hidden="true"
          className={cn("absolute h-4 w-4 transition-all duration-150 motion-reduce:transition-none",
            visible ? "scale-100 opacity-100" : "scale-75 opacity-0")} />
      </button>
    </div>
  );
});

export default PasswordInput;
