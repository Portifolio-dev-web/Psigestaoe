import * as React from "react"
import { ChevronDown } from "lucide-react"

import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Lista de seleção ÚNICA do sistema (análise de UI, F4).
//
// Antes havia dois tipos: o <select> nativo, com estilo próprio em cada tela
// (cadastro de paciente, filtros da auditoria, ordem da lista), e a lista
// estilizada do Radix (sessão da agenda, bloqueio por inatividade), com
// aparência e comportamento diferentes. Agora todas usam este componente:
//   - é o <select> do navegador: no celular abre o seletor nativo (rolagem
//     grande, fácil de tocar) e funciona igual com teclado e leitor de tela;
//   - tem a mesma aparência dos campos de texto (altura, borda, foco).
// Props:
//   - className: layout por fora (margem, largura), ex.: "mt-1.5 w-56";
//   - selectClassName: estilo do próprio campo, ex.: borda vermelha de erro;
//   - as demais vão para o <select> (id, value, onChange, aria-*, required...).
// ---------------------------------------------------------------------------
const NativeSelect = React.forwardRef(({ className, selectClassName, children, ...props }, ref) => (
  <div className={cn("relative", className)}>
    <select
      ref={ref}
      className={cn(
        "flex h-9 w-full appearance-none rounded-md border border-input bg-background py-1 pl-3 pr-9 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        selectClassName
      )}
      {...props}>
      {children}
    </select>
    <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 opacity-60" aria-hidden="true" />
  </div>
))
NativeSelect.displayName = "NativeSelect"

export { NativeSelect }
