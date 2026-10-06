import { useTheme } from "next-themes"
import { Toaster as Sonner, toast } from "sonner"

// ---------------------------------------------------------------------------
// Acessibilidade (análise de UI, A14 e F1): quanto tempo cada aviso fica na tela.
// Antes TODOS sumiam em 4 s (padrão da biblioteca): um erro longo ("Faltando:
// CPF, RG, ...") sumia antes de ser lido, e o "Desfazer" depois de remarcar
// ou remover uma sessão dava 4 s para reagir. Regras, aplicadas aqui para
// todas as telas (cada chamada pode passar o próprio `duration`):
//   - sucesso/informação: 5 s;   alerta: 8 s;   erro: 10 s;
//   - aviso com ação ("Desfazer", "Registrar evolução"...): pelo menos 10 s;
//   - texto longo: tempo de leitura (~60 ms por caractere), até 20 s.
// O aviso também pausa com o mouse em cima e com o foco do teclado (Alt+T).
// Os erros de FORMULÁRIO não usam aviso: ficam no próprio formulário.
// ---------------------------------------------------------------------------
const MIN_DURATION = { success: 5000, info: 5000, message: 5000, warning: 8000, error: 10000 }
const ACTION_MIN_DURATION = 10000
const MAX_COMPUTED_DURATION = 20000

function withReadableDuration(show, base) {
  return (message, options = {}) => {
    if (options.duration !== undefined) return show(message, options)
    const reading = 3000 + String(message ?? "").length * 60
    const duration = Math.min(MAX_COMPUTED_DURATION, Math.max(base, options.action ? ACTION_MIN_DURATION : 0, reading))
    return show(message, { ...options, duration })
  }
}

// O `toast` da biblioteca é um objeto único, compartilhado por todo o app:
// ajustá-lo aqui (uma vez, ao carregar) vale para todas as chamadas.
if (!toast.__psiReadable) {
  Object.entries(MIN_DURATION).forEach(([type, base]) => {
    toast[type] = withReadableDuration(toast[type], base)
  })
  toast.__psiReadable = true
}

const Toaster = ({
  ...props
}) => {
  const { theme = "system" } = useTheme()

  return (
    <Sonner
      theme={theme}
      className="toaster group"
      // Acessibilidade (análise de UI, A6): a área dos avisos era anunciada pelo
      // leitor de tela como "Notifications alt+T" (em inglês). Alt+T continua
      // levando o foco aos avisos.
      containerAriaLabel="Avisos"
      toastOptions={{
        closeButtonAriaLabel: "Fechar aviso",
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props} />
  );
}

export { Toaster, toast }
