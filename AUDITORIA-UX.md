# Auditoria de UX — 20 Leis de UX (lawsofux.com) — PsiGestão

**Data:** 2026-11-09 · **Método:** leitura do código-fonte do frontend (React + Tailwind + CRACO), cruzada com o relatório de acessibilidade já existente (`UX_A11Y_AUDIT_REPORT.md`, WCAG 2.2) para evitar repetir achados — este relatório usa outro referencial (as 20 Leis de UX) e só cita o anterior quando é relevante para uma lei específica. Nenhum arquivo de código foi alterado nesta etapa.

---

## ETAPA 1 — Mapeamento

**Stack:** React 18 + React Router 6 + TailwindCSS + CRACO, componentes Radix UI (`shadcn/ui` em `components/ui/*`), `sonner` para toasts, `date-fns` para datas, ícones `lucide-react`. Backend FastAPI/Gunicorn (Python) — fora do escopo desta auditoria visual, citado só onde afeta uma lei (ex.: validação de CPF).

**Rotas** (`frontend/src/App.js`):
| Rota | Página | Autenticação |
|---|---|---|
| `/` | Landing | Pública (redireciona se logado) |
| `/login`, `/register` | Login (mesmo componente, dois modos) | Pública |
| `/esqueci-senha`, `/criar-senha`, `/redefinir-senha` | ForgotPassword / EmailLinkPassword | Pública |
| `/privacidade`, `/termos` | PrivacyPolicy / TermsOfUse | Pública (também logado) |
| `/dashboard` | Dashboard ("Painel") | Protegida |
| `/pacientes`, `/pacientes/:id` | Patients / PatientDetail | Protegida |
| `/agenda` | Agenda | Protegida |
| `/configuracoes` | Settings (6 abas) | Protegida |
| `/ajuda` | Help | Protegida e pública |
| `/assinatura/simulacao` | SimulatedCheckout | Protegida (dev only) |
| `/administracao` | Admin | Protegida (admin) |
| `/paciente-cadastro/*` | PatientIntake | Pública, fora do `AuthProvider` |
| `*` | NotFound (404) | — |

**Componentes de UI principais:** `components/ui/*` (Button, Input, Dialog, AlertDialog, Sheet, Tabs, Table, NativeSelect, Command, Badge, Skeleton, Sonner/Toaster) — biblioteca única reutilizada em todo o app; `AppLayout` (casca autenticada: menu lateral desktop + gaveta mobile); `PublicPageShell` (casca pública); `PatientFormFields` (campos da ficha, compartilhados entre cadastro manual e link do paciente).

**Formulários:** Login/Cadastro (`AuthCard`+`Login.js`), Esqueci/Redefinir senha, Cadastro/edição de paciente (`PatientFormFields`, ~13 campos + consentimento), Cadastro pelo próprio paciente (`PatientIntake`, mesmos campos), Sessão da agenda (`SessionDialog`), Evolução clínica (`RecordEditorDialog`, com rascunho automático), Configurações (perfil profissional, termo, senha, 2FA).

**Fluxos centrais:** (1) Cadastro/login da psicóloga; (2) Cadastro de paciente (manual ou link enviado ao paciente); (3) Agendamento de sessão (única ou recorrente); (4) Registro de evolução clínica (com rascunho); (5) Assinatura/pagamento (simulado em dev); (6) Configurações/conformidade (termo, 2FA, exportação, encerramento de conta).

---

## Resumo geral

### Nota: **8,3 / 10**

O projeto já passou por pelo menos duas rodadas de revisão de UX/acessibilidade documentadas no próprio código (comentários `// Análise de UI`, `// UX`, `// Correção`) e por uma auditoria WCAG anterior com todos os achados corrigidos. O resultado é um app incomum em maturidade de UX para o estágio do produto: estados de carregamento em todo lugar, rascunho automático na tarefa mais crítica (evolução clínica), formulários em blocos com erro inline, e feedback positivo específico em cada ação. Os pontos que restam são reais, mas pontuais — não há lei das 20 claramente ignorada.

### 3 pontos mais fortes
1. **Zeigarnik + Gradiente de Objetivo, combinados de verdade.** `components/FirstSteps.js` mostra "3 de 4 concluídos" com barra de progresso, e cada passo pendente é um link direto à ação que falta. `components/RecordEditorDialog.js` salva rascunho criptografado localmente enquanto a psicóloga escreve a evolução — perder a aba, a sessão expirar ou a rede cair não apaga o texto (comentário de design em `RecordEditorDialog.js:20-44`).
2. **Doherty (limiar de resposta).** Loading screens, `Skeleton` em painel/lista/agenda, busca de pacientes com debounce de 300 ms (`Patients.js:88,188-190`), carregamento de telas sob demanda (`React.lazy` em todas as rotas, `App.js:19-40`), e proteção contra duplo clique em ações destrutivas (`useSingleFlight`, `Patients.js:105,352-387`).
3. **Miller + Proximidade, no formulário mais longo do sistema.** A ficha do paciente (13+ campos) é dividida em seções com `<fieldset>`/`<legend>` (Identificação, Responsável legal, Contato, Informações clínicas, Consentimento — `PatientFormFields.js:33-42`), two colunas no desktop e uma no celular, com erro de validação ligado a cada campo (`aria-describedby`) e resumo navegável no topo (`FormErrorSummary`, `PatientFormFields.js:53-82`).

### 3 pontos mais críticos
1. **Tesler (Lei da Conservação da Complexidade) — endereço 100% manual.** O campo "Endereço" é um único `<Input>` de texto livre (`PatientFormFields.js:325-340`): rua, número, bairro e cidade tudo digitado à mão, sem busca por CEP. É o único dado de contato sem nenhuma automação, enquanto CPF e telefone já têm máscara ao vivo (`lib/validation.js`).
2. **Postel (Lei da Robustez) — CPF obrigatório mesmo para menores.** O campo CPF é `required` incondicionalmente (`PatientFormFields.js:131-146`), mas RG, estado civil e profissão já ficam opcionais/escondidos para menores de idade (`minor`, mesmo arquivo, linhas 153, 188, 224). Uma criança sem CPF emitido ainda trava o cadastro.
3. **Jakob (convenção) — o logo não leva à página inicial.** Em `AppLayout.js:64-73`, o bloco com o ícone e "PsiGestão" no topo do menu é um `<div>` simples, não um link — quebra a convenção mais antiga da web (clicar na marca volta ao início).

---

## ETAPA 2 — Avaliação das 20 Leis

| Lei | Status | Evidência (arquivo:linha) | Problema | Recomendação | Prioridade |
|---|---|---|---|---|---|
| **1. Jakob** | ⚠️ Parcial | `components/AppLayout.js:64-73` (logo sem link); nav com rótulos e ícones convencionais em `AppLayout.js:20-34` | O bloco do logo/nome no topo do menu lateral é um `<div>`, não um link para `/dashboard` — convenção universal da web quebrada. O resto (menu lateral fixo, ícones padrão, posição do avatar/logout no rodapé) segue o padrão esperado. | Envolver o bloco do logo em `<Link to="/dashboard">` (ou `<NavLink>`), com `aria-label="Ir para o Painel"`. | Baixa |
| **2. Fitts** | ⚠️ Parcial | `components/ui/button.jsx:23-28` (`sm`=32px, `default`=36px, `icon`=36px); `pages/Patients.js:783` (menu "⋮" 32×32px em linha de tabela/cartão) | Os tamanhos padrão de botão ficam entre 32–36px — acima do mínimo WCAG (24px, já corrigido na auditoria A11Y anterior) mas abaixo dos 44px recomendados para toque confortável, especialmente em botões de ação isolados no celular (menu de paciente, "Bloquear"). | Nos contextos de toque primário (linha de tabela/cartão no celular, botão "Bloquear" do cabeçalho), usar `size="lg"` ou uma variante só para mobile com `h-11 w-11`. | Média |
| **3. Hick** | ✅ Atende | `pages/Settings.js:794-870` (6 abas, não uma tela só); `components/agenda/agendaUtils.js:62-66` (recorrência: só "semanal"/"quinzenal"); `pages/Patients.js:71-75` (3 opções de ordenação) | — | Nenhuma ação necessária. As listas de opções do sistema (estado civil, escolaridade, recorrência, ordenação) são curtas e com rótulo claro; a tela de Configurações, que reúne o maior número de funções, usa abas em vez de uma lista única. | — |
| **4. Miller** | ✅ Atende | `components/PatientFormFields.js:33-42,94-240` (ficha em seções com `fieldset`/`legend`) | — | Nenhuma ação necessária. Ver "3 pontos mais fortes". | — |
| **5. Tesler** | ❌ Não atende (parcial no restante) | `components/PatientFormFields.js:325-340` (endereço, um campo de texto livre); contraponto: `lib/validation.js:32-49` (máscara de CPF e telefone ao vivo) | O sistema não busca bairro/cidade/UF a partir do CEP — complexidade que poderia ser absorvida pelo software (como já é feito com a máscara de CPF/telefone) fica com quem digita. | Adicionar campo "CEP" com busca automática (ex.: ViaCEP, API pública e gratuita, sem chave) que preenche rua/bairro/cidade/UF, deixando "número" e "complemento" editáveis. Ver código sugerido na seção de correções. | Média |
| **6. Postel** | ⚠️ Parcial | `components/PatientFormFields.js:131-146` (CPF sempre obrigatório) vs. `:153,188,224` (RG/estado civil/profissão opcionais para menores); `lib/validation.js:66-70` (telefone aceita 10–15 dígitos, com ou sem "+") | CPF é o único dado de identificação que não abre exceção para menores de idade, mesmo quando RG e outros já abrem. Telefone, por outro lado, é um bom exemplo de entrada tolerante (aceita formatos variados e corrige a máscara). | Tornar CPF do paciente opcional quando `isMinorForm(form)` for verdadeiro (replicar o padrão já usado em `rg`), mantendo a mesma regra no backend (`backend/server.py`, fora do escopo desta auditoria, mas a mudança só funciona se os dois lados combinarem). | Média |
| **7. Doherty** | ✅ Atende | `pages/Patients.js:88,188-193` (debounce de busca); `components/ui/skeleton.jsx` usado em `Dashboard.js:119,199`; `App.js:19-48` (lazy loading + tela de carregamento); `hooks/useSingleFlight.js` via `Patients.js:105` | — | Nenhuma ação necessária. | — |
| **8. Navalha de Occam** | ⚠️ Parcial | Cor de marca `#1E3A8A` repetida **229 vezes em 50 arquivos** (grep), contra só **5 arquivos** usando a classe de token `bg-primary`, embora o token exista (`index.css:25`, `--primary: 224 64% 33%`) | O design já define variáveis de tema (`--primary`, `--secondary` etc., usadas pelo Tailwind/shadcn), mas a maioria das telas escreve a cor da marca como texto solto (`bg-[#1E3A8A] hover:bg-[#0F2C59]`) em vez de usar o token. Funciona hoje porque todo mundo copiou o mesmo valor, mas uma mudança de marca exigiria editar dezenas de arquivos em vez de uma variável. | Não é urgente trocar tudo de uma vez — ao tocar em qualquer componente que já tenha essa cor, preferir `variant="default"` do `Button` (já usa `bg-primary`) ou a classe `bg-primary`/`text-primary` em vez do hexadecimal. | Baixa |
| **9. Pareto** | ✅ Atende | `pages/Dashboard.js:176-195` ("Hoje" e "Primeiros passos" aparecem antes dos totais); `components/AppLayout.js:20-27` (Painel/Pacientes/Agenda são os 3 primeiros itens do menu) | — | Nenhuma ação necessária. As ações mais frequentes (ver agenda do dia, abrir ficha, registrar evolução) ficam a 1 clique do Painel. | — |
| **10. Estética-Usabilidade** | ⚠️ Parcial | Mesma evidência da lei 8 (Occam): tokens definidos em `index.css:17-47` mas pouco usados; tipografia consistente (`font-head`/Manrope para títulos, IBM Plex Sans para corpo, `index.css:5-15`) | O resultado visual atual é coeso (mesma paleta, mesmos raios de borda, mesma fonte), mas a base é menos sólida do que parece — a consistência depende de todo mundo copiar o mesmo valor manualmente, não de uma fonte única. | Mesma recomendação da lei 8. | Baixa |
| **11. Pico-Final** | ✅ Atende | `pages/NotFound.js` (404 com explicação e saída clara); `toast.success` com mensagens específicas por ação (ex.: `Patients.js:355,370,373,313,318,321`, nunca um genérico "sucesso") | 🔍 Validar em uso: a tela de pagamento real (quando a simulação for substituída por um gateway de verdade) precisa manter o mesmo cuidado — hoje só existe a versão simulada (`pages/SimulatedCheckout.js`). | Ao integrar o gateway real, manter uma tela de confirmação com o mesmo padrão de clareza do 404/toasts atuais. | — |
| **12. Von Restorff** | ✅ Atende | `pages/Patients.js:462-467` (CTA "Novo paciente" sólido azul vs. "Link de cadastro" em `outline`); `pages/Patients.js:761,798` (ação "Excluir" em vermelho, destacada das demais no menu) | — | Nenhuma ação necessária. | — |
| **13. Posição Serial** | ✅ Atende | `components/AppLayout.js:20-34` (itens mais usados no topo do menu: Painel, Pacientes, Agenda) e `:116-135` (Sair e links legais no rodapé, separados por borda) | — | Nenhuma ação necessária. | — |
| **14. Zeigarnik** | ✅ Atende | `components/RecordEditorDialog.js:20-44` (rascunho automático da evolução clínica); `pages/Dashboard.js:283-302` (aviso de cadastros incompletos de pacientes, com prazo) | — | Nenhuma ação necessária. Ver "3 pontos mais fortes". | — |
| **15. Gradiente de Objetivo** | ✅ Atende | `components/FirstSteps.js:78-85` (barra de progresso + "X de Y concluídos") | — | Nenhuma ação necessária. | — |
| **16. Proximidade** | ✅ Atende | `components/PatientFormFields.js:33-42` (rótulo, campo e erro sempre no mesmo bloco, `mt-1.5`/`mt-1` entre eles) | — | Nenhuma ação necessária. | — |
| **17. Similaridade** | ✅ Atende | Biblioteca única de componentes (`components/ui/button.jsx`, `input.jsx`, `card.jsx`) importada em praticamente todas as telas — mesmo elemento visual para a mesma função em todo o sistema. | — | Nenhuma ação necessária. | — |
| **18. Região Comum** | ✅ Atende | `components/ui/card.jsx` usado para agrupar cada bloco (estatística, paciente, seção de configurações); `components/PatientFormFields.js:720` (bloco de consentimento com fundo e borda próprios, `pages/Patients.js`) | — | Nenhuma ação necessária. | — |
| **19. Conectividade Uniforme** | ✅ Atende | `pages/Dashboard.js:29-46` (`StatCard` inteiro é um link para a tela relacionada, com `hover:-translate-y-[1px]`); `components/PatientAgendaCard.js` liga sessão da agenda à ficha do paciente | — | Nenhuma ação necessária. | — |
| **20. Prägnanz** | ✅ Atende | Ícones `lucide-react` com traço fino e consistente (`strokeWidth={1.6}`/`1.7}` repetido em `AppLayout.js:89`, `Dashboard.js:34`); raio de borda único via `--radius` (`index.css:46`) | — | Nenhuma ação necessária. | — |

**Desktop vs. mobile:** quase todas as telas inspecionadas usam breakpoints Tailwind consistentes (`sm:`/`md:`/`lg:`) com padrões já documentados em comentário no próprio código (ex.: `Patients.js:456-458`, `PatientTable`/`PatientMobileCards` como duas renderizações paralelas — tabela no desktop, cartões no celular, `Patients.js:568-646`). Isso não é um "ajuste responsivo" feito depois: é um padrão repetido (Dashboard, Agenda citada no relatório A11Y anterior). 🔍 Validar em uso: zoom de 400% e orientação paisagem em celular pequeno não foram verificados nesta rodada (auditoria baseada em código, não em captura de tela real) — o relatório `UX_A11Y_AUDIT_REPORT.md` já cobriu reflow em 320px via `axe-core`, sem rolagem horizontal encontrada.

---

## ETAPA 3 — Correções com melhor relação impacto × esforço

### 1. Jakob — logo como link para o início

```jsx
// components/AppLayout.js (dentro de SidebarContent)
// Antes:
<div className="flex items-center gap-2.5 px-6 py-6">
  <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/15">
    <img src={logoIcone} alt="" className="h-6 w-auto" />
  </div>
  <div className="leading-tight">
    <p className="font-head text-lg font-800 font-bold text-white">PsiGestão</p>
    <p className="text-[11px] text-blue-200">Prontuários de Psicologia</p>
  </div>
</div>

// Depois:
<Link to="/dashboard" onClick={() => setOpen(false)}
  className="flex items-center gap-2.5 px-6 py-6 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white rounded-md"
  aria-label="Ir para o Painel">
  <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/15">
    <img src={logoIcone} alt="" className="h-6 w-auto" />
  </div>
  <div className="leading-tight">
    <p className="font-head text-lg font-800 font-bold text-white">PsiGestão</p>
    <p className="text-[11px] text-blue-200">Prontuários de Psicologia</p>
  </div>
</Link>
```
*(`Link` já está importado via `NavLink` de `react-router-dom` no topo do arquivo; só falta importar `Link` também.)*

### 2. Fitts — alvos de toque maiores no celular para ações isoladas

```jsx
// components/ui/button.jsx — nova variante de tamanho, só para toque primário isolado
size: {
  default: "h-9 px-4 py-2",
  sm: "h-8 rounded-md px-3 text-xs",
  lg: "h-10 rounded-md px-8",
  icon: "h-9 w-9",
  "icon-lg": "h-11 w-11", // novo: 44px, para ações isoladas (menu "⋮" no celular, "Bloquear")
},
```
Aplicar `size="icon-lg"` só nos botões-ícone que ficam sozinhos (sem texto ao lado) em telas de toque, como o menu `⋮` de cada paciente (`pages/Patients.js:783`).

### 3. Postel — CPF opcional para pacientes menores

```jsx
// components/PatientFormFields.js (trecho do campo CPF, dentro de FormSection "Identificação")
// Antes:
<Label htmlFor="patient-cpf">CPF *</Label>
<Input id="patient-cpf" required ... />

// Depois (mesmo padrão já usado no campo RG, linha 153):
<Label htmlFor="patient-cpf">CPF{mark("cpf")}</Label>
<Input id="patient-cpf" required={!minor} ... />
```
**Atenção:** isso só resolve a tela. O servidor (`backend/server.py`, não auditado em detalhe nesta rodada) precisa aceitar CPF vazio para pacientes menores antes de liberar esta mudança — do contrário o formulário permite enviar, mas a API devolve erro 422 mesmo assim.

### 4. Tesler — busca de endereço por CEP

```jsx
// components/PatientFormFields.js — substituir o campo único "Endereço" por CEP + campos
// Novo campo antes do endereço (dentro de FormSection "Contato"):
<div>
  <Label htmlFor="patient-cep">CEP</Label>
  <Input id="patient-cep" inputMode="numeric" maxLength={9} value={form.cep}
    onChange={onFormChange("cep")}
    onBlur={async () => {
      const digits = onlyDigits(form.cep);
      if (digits.length !== 8) return;
      try {
        const { data } = await fetch(`https://viacep.com.br/ws/${digits}/json/`).then((r) => r.json());
        if (!data?.erro) {
          onFormChange("address")({ target: { value: `${data.logradouro}, ${data.bairro}, ${data.localidade}/${data.uf}` } });
        }
      } catch { /* sem CEP automático, a pessoa preenche à mão (já funciona hoje) */ }
    }}
    placeholder="00000-000" className="mt-1.5" data-testid="patient-cep-input" />
</div>
{/* campo "Endereço" existente continua, já preenchido e editável (número/complemento à mão) */}
```
*Ponytail: ViaCEP é gratuito, sem chave e sem dependência nova — só um `fetch`. O endereço manual continua funcionando se o CEP falhar ou não for preenchido (sem quebrar cadastros antigos).*

### 5. Occam / Estética-Usabilidade — reduzir a duplicação de cor (começar pelo que já está sendo tocado)

```jsx
// Não é uma migração de uma vez (229 ocorrências = risco de quebrar algo sem necessidade).
// Regra para qualquer edição futura num botão primário:
// Antes:
<Button className="bg-[#1E3A8A] hover:bg-[#0F2C59]">Salvar</Button>
// Depois (o token já produz a MESMA cor, variant="default" já é o padrão do Button):
<Button>Salvar</Button>
```
Ao tocar em qualquer arquivo por outro motivo, remover o `className` de cor se ele só repetir o que `variant="default"` já faz — sem sessão dedicada de refatoração.

---

## Quick wins (menos de 30 minutos cada)

1. **Logo clicável** (correção 1 acima) — ~10 min.
2. **CPF opcional para menores no formulário** (correção 3, só o lado do frontend) — ~10 min, mas só entra em produção depois de confirmar a regra no backend.
3. **Botão "⋮" do paciente em 44px no celular** — adicionar a variante `icon-lg` (correção 2) e aplicá-la em `RowMenu` (`pages/Patients.js:783`) — ~15 min.
4. **Padronizar `title`/`aria-label` do campo CRP** (`pages/Settings.js:127-129`) — já tem `pattern` e `title`, mas o `title` só aparece em navegadores que mostram tooltip de validação; adicionar um `<p className="text-xs text-slate-500">` fixo abaixo do campo com o mesmo texto ajuda quem usa o celular (sem tooltip de `title` no toque) — ~10 min.
5. **Opção "Selecione..." do `NativeSelect` com `disabled`** (`PatientFormFields.js:199,218`) — hoje é uma opção comum da lista; marcá-la `disabled` evita que alguém volte a selecioná-la por engano depois de já ter escolhido outra — ~5 min, repetir nos ~6 selects do sistema.

---

*Relatório gerado por leitura de código (sem alterar nenhum arquivo do produto). Leis marcadas 🔍 "Validar em uso" pedem teste manual (zoom 400%, leitor de tela, ou a tela de pagamento real quando existir) — o relatório `UX_A11Y_AUDIT_REPORT.md` já cobre parte disso sob o recorte WCAG.*
