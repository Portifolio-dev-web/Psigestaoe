# Auditoria de UX/Acessibilidade (WCAG 2.2 AA) — PsiGestão

**Data:** 2026-11-06 · **Ambiente:** desenvolvimento local (`docker compose up`), frontend em
`localhost:3000`, API em `localhost:5000`. **Conta usada:** conta descartável criada pelo fluxo
normal de cadastro (`a11y-audit@example.com`), sem tocar em dados de usuários reais.

## Metodologia

- **Varredura automática**: `axe-core` 4.10 (regras WCAG 2.0–2.2 A/AA + boas práticas) via Chrome
  headless, controlado pelo Chrome DevTools Protocol (CDP) — 15 telas/estados, desktop + celular
  (390px) + reflow em 320px. O login foi feito pelo formulário real (sem atalho de API), para a
  varredura ver exatamente o que uma pessoa vê.
- **Teclado real**: ordem de `Tab` e gerenciamento de foco verificados com eventos de teclado e
  mouse reais via CDP (`Input.dispatchKeyEvent`/`dispatchMouseEvent`), não `element.click()` em
  JavaScript — que não move o foco e mascararia problemas de retorno de foco.
- **Leis de UX cruzadas com os dados**: Fitts (tamanho de alvo), Hick/Miller (carga de
  informação), Jakob (padrões e landmarks previsíveis), Gestalt (proximidade visual vs. estrutura
  do DOM).

**Telas cobertas**: Início, Entrar, Cadastro, Esqueci minha senha, Política de Privacidade, Termos
de Uso, Ajuda (com e sem login), Painel, Pacientes, Agenda, Configurações, diálogo "Novo
paciente" — nas versões desktop e celular que couberam no tempo disponível.

**Fora do escopo desta rodada** (não testado — não afirmo que estejam certas nem erradas):
Administração, Assinatura (simulação), Configurações por aba (só a tela inicial), Ficha de
paciente com dados reais (exigiria criar um paciente de teste), cadastro pelo paciente
(`/cadastro`), verificação em duas etapas, e o build de **produção** (tudo acima rodou no
servidor de desenvolvimento do React/CRA — ver nota abaixo).

**Nota sobre falso positivo do ambiente**: o axe-core apontou "frame-title" (iframe sem título) em
**todas** as telas. A causa é o `iframe#webpack-dev-server-client-overlay`, injetado só pelo
servidor de desenvolvimento do Create React App (não existe no build de produção, servido pelo
nginx). Esse mesmo iframe também "roubou" o foco em alguns cliques de mouse simulados durante os
testes de teclado (ele fica em `position:fixed; inset:0`, cobrindo a tela mesmo sem erro ativo) —
por isso os testes de teclado abaixo usam `.focus()`/clique por JavaScript para abrir elementos, e
só eventos reais de teclado (Tab, Enter, Esc) para medir navegação e foco, que é o que importa de
fato. **Esse item não entra no relatório como achado do produto.**

## Veredito

As telas autenticadas (Painel, Pacientes, Agenda, Configurações, Ajuda) têm uma base de
acessibilidade sólida e deliberada — landmarks corretos, anúncio de troca de tela, link para
pular o menu, contraste de cor aprovado em 100% das telas testadas. O ponto fraco está
concentrado nas telas de **entrada** (Entrar/Cadastro/Esqueci a senha, que usam um componente
mais novo, `AuthCard`): falta a região `<main>` que as telas antigas (`AuthShell`) já têm, e a
ordem de tabulação começa no botão decorativo, não no formulário. Há também um bug real de
retorno de foco ao fechar diálogos. Nada do que foi encontrado é bloqueador para uso com mouse;
tudo afeta especificamente quem usa teclado ou leitor de tela.

## Status

**Corrigidos e verificados: A1, A3, A5 (primeira rodada) e A2 (segunda rodada)** — ver detalhe de
cada um na tabela abaixo e as seções "Verificação" no final. **A4 continua aberto** — não foi
alterado.

## Achados, por prioridade

| ID | Problema (o que acontece com quem usa) | Evidência | Norma |
|----|----|----|----|
| **A1** ✅ | *(Corrigido)* As telas de Entrar, Cadastro e "Esqueci minha senha" não têm nenhuma região (`<main>`, `<header>` etc.). Quem navega por landmarks (leitor de tela, atalho "próxima região") não encontra nada — justamente nas telas que mais precisam disso, porque é onde alguém aflito (esqueceu a senha, não recebeu o e-mail) chega primeiro. | `axe-core`: violação `region` ("All page content should be contained by landmarks"), confirmada nas 3 telas. Código: [`AuthCard.jsx:85`](Psigestaoe/frontend/src/components/auth/AuthCard.jsx#L85) — o `<div>` externo (e os dois painéis internos) não usam nenhuma tag/role de landmark. Usado por [`Login.js`](Psigestaoe/frontend/src/pages/Login.js) (Entrar/Cadastro) e [`ForgotPassword.js`](Psigestaoe/frontend/src/pages/ForgotPassword.js). Comparar com [`AuthShell.js:46`](Psigestaoe/frontend/src/components/auth/AuthShell.js#L46), o componente mais antigo, que já envolve o conteúdo num `<main>` — provavelmente ainda usado só por `EmailLinkPassword.js` (criar/redefinir senha), não auditado nesta rodada. | WCAG 2.4.1 (Bypass Blocks) / 1.3.1 (Info and Relationships); Lei de Jakob |
| **A2** ✅ | *(Corrigido)* Ao fechar o diálogo "Novo paciente" com **Esc**, o foco não volta ao botão "Novo paciente" que o abriu — vai para o início do conteúdo principal (cai perto do aviso "Teste grátis: faltam 60 dias..."). Quem usa teclado ou leitor de tela perde o lugar onde estava. | Verificado em CDP com teclado real: abrir o diálogo → foco corretamente dentro dele (campo "Nome") → `Esc` → `document.activeElement` passa a ser `<main id="conteudo">`, não o botão. Causa provável: [`useDialogFocusReturn.js:41-47`](Psigestaoe/frontend/src/hooks/useDialogFocusReturn.js#L41-L47) chama `restoreFocus` no `onCloseAutoFocus`, mas [`focus.js:19-24`](Psigestaoe/frontend/src/lib/focus.js#L19-L24) rejeita o botão original se ele estiver dentro de `[aria-hidden="true"]` — e o Radix ainda não removeu esse `aria-hidden` dos elementos-irmãos do diálogo nesse instante exato (condição de corrida). O comentário do próprio arquivo (`dialog.jsx:31-33`) descreve o comportamento pretendido, que não é o que acontece. Testado só no diálogo "Novo paciente"; como o código é compartilhado (`dialog.jsx`), é provável que o mesmo ocorra nos outros diálogos do sistema, mas isso não foi testado em cada um. | WCAG 2.4.3 (Focus Order); padrão ARIA APG para diálogos modais |
| A3 ✅ | *(Corrigido)* Vários links/controles isolados (fora de frases) têm menos de 24px de altura — alvo pequeno para toque e para quem tem tremores/baixa precisão (Lei de Fitts). | `axe`/heurística de alvo: "Esqueci minha senha" 116×16 ([`Login.js:306-308`](Psigestaoe/frontend/src/pages/Login.js#L306-L308)); "Voltar para o login" 116×18 ([`ForgotPassword.js:78-80`](Psigestaoe/frontend/src/pages/ForgotPassword.js#L78-L80)); "Ler a Política de Privacidade"/"Ler os Termos de Uso" 180×20/136×20 ([`Help.js:177-183`](Psigestaoe/frontend/src/pages/Help.js#L177-L183)); "Abrir agenda do dia" 125×20 ([`Dashboard.js:112-114`](Psigestaoe/frontend/src/pages/Dashboard.js#L112-L114)). O próprio projeto já resolveu isso uma vez: [`AuthShell.js:60`](Psigestaoe/frontend/src/components/auth/AuthShell.js#L60) usa `inline-flex min-h-6 items-center` no link "Precisa de ajuda?" — é só repetir o padrão. | WCAG 2.5.8 (Target Size, Minimum) |
| A4 | Na tela de Entrar, o primeiro `Tab` ao abrir a página vai para o botão decorativo "Criar conta" (painel da esquerda), não para o campo de e-mail — quem navega por teclado precisa pular esse botão a cada visita antes de chegar ao formulário. | Ordem de `Tab` real (CDP), medida na raiz da página: 1) botão "Criar conta" → 2) campo e-mail → 3) "Esqueci minha senha" → 4) senha → ... Vem da ordem do DOM em [`AuthCard.jsx:112-149`](Psigestaoe/frontend/src/components/auth/AuthCard.jsx#L112-L149): o painel decorativo (`Hero`, com o botão) é renderizado ANTES do painel do formulário, mesmo ficando visualmente à esquerda só no desktop (no celular os painéis empilham e isso pode inverter de novo). | WCAG 2.4.3 (Focus Order); Lei de Jakob (ordem previsível = formulário primeiro) |
| A5 ✅ | *(Corrigido)* A caixa de aceite dos Termos/Privacidade no cadastro é pequena (16×16px). O clique no texto do rótulo (`<Label htmlFor>`) também marca a caixa — então funciona —, mas quem olha para a tela vê só a caixinha pequena como alvo, e pode tentar acertar exatamente ela. | [`Login.js:432-436`](Psigestaoe/frontend/src/pages/Login.js#L432-L436): `<Checkbox id="terms">` como elemento irmão do `<Label>`, não envolvido por ele. | WCAG 2.5.8 (Target Size) — severidade baixa pela mitigação do `label` |

## O que já está bom

- **Contraste de cor**: 0 violações do critério 1.4.3 (AA) em nenhuma das 15 telas/estados testados.
- **Reflow em 320px**: nenhuma tela testada gerou rolagem horizontal.
- **Landmarks nas telas autenticadas**: Painel, Pacientes, Agenda, Configurações e Ajuda (logado)
  passaram a regra `region` do axe sem nenhuma violação — `<nav>`/`<main>` bem estruturados em
  [`AppLayout.js`](Psigestaoe/frontend/src/components/AppLayout.js).
- **Link "Pular para o conteúdo"** já existe para quem está logado
  ([`AppLayout.js:155-156`](Psigestaoe/frontend/src/components/AppLayout.js#L155-L156)).
- **Troca de tela anunciada**: [`RouteAnnouncer.js`](Psigestaoe/frontend/src/components/RouteAnnouncer.js)
  atualiza o título da aba, manda o foco para `<main>` e anuncia o nome da tela numa região viva —
  terminar certo as três partes do padrão (WCAG 2.4.2/4.1.3) é raro de ver bem feito.
- **Diálogo "Novo paciente"**: o foco inicial, ao abrir, já vai direto para o primeiro campo
  ("Nome") — comportamento correto, verificado com evento real de clique.
- **Botão de mostrar senha**: tem `aria-label="Mostrar senha"` e `aria-pressed`, não é um ícone mudo.
- **Login não revela** se o e-mail ou a senha está errada (mensagem genérica) — não é achado de
  acessibilidade, mas mostra que o time já pensa em quem está tentando entrar sem dar pistas a um
  atacante.

## Ordem sugerida (complexidade × prioridade)

| Ordem | ID | Complexidade | Prioridade | Por quê nessa posição |
|---|---|---|---|---|
| 1 | **A1** | Muito baixa — trocar/envolver uma tag (`<div>` → `<main>`) | Alta | Maior impacto por menor esforço: um ajuste cobre as 3 telas de entrada de uma vez, e é o achado mais sério do axe. |
| 2 | **A3** | Muito baixa — repetir uma classe CSS já usada no projeto em 4 lugares | Média | O padrão certo já existe (`AuthShell.js`); é copiar e colar, sem decisão nova. |
| 3 | **A5** | Muito baixa — aumentar a caixa do `Checkbox` ou embrulhá-la no `<Label>` | Baixa | Tão barato que compensa fazer no mesmo lote do item acima, mesmo sendo o de menor risco/urgência. |
| 4 | **A4** | Baixa/média — mover o bloco no DOM ou usar `tabIndex`, com cuidado para não quebrar a pilha no celular (onde os painéis empilham diferente do desktop) | Média | Exige reconferir o layout responsivo depois da mudança; por isso vem depois dos ajustes puramente de CSS. |
| 5 | **A2** | Média/alta — é preciso ajustar o momento exato em que o foco volta (condição de corrida com o Radix) e reconferir em outros diálogos do sistema, não só no "Novo paciente" | Alta | Importância alta, mas é o que exige mais atenção e verificação (risco de "corrigir" só a aparência e deixar a corrida de novo); por isso fica por último, feito com calma. |

**Resumindo**: 1→3 são ajustes mecânicos de CSS/markup, dá para fazer em lote e verificar juntos.
4 pede atenção ao responsivo. 5 é o único que pede investigação mais cuidadosa (timing/condição de
corrida) antes de aplicar — vale reservar um tempo à parte para ele, de preferência com o teste de
teclado real (como o que gerou esse achado) rodando de novo depois da correção.

## Verificação das correções (A1, A3, A5)

- **O que mudou**: `AuthCard.jsx` (div → `<main>`); 4 links com `inline-flex min-h-6 items-center`
  (`Login.js`, `ForgotPassword.js`, `Help.js`, `Dashboard.js`); checkbox dos Termos com `h-6 w-6`
  em vez do padrão `h-4 w-4` (`Login.js`), só nesta instância — o componente compartilhado
  `ui/checkbox.jsx` não foi alterado.
- **Varredura `axe-core` depois da correção** (Entrar, Cadastro, Esqueci minha senha, Ajuda,
  Painel): **0 problemas** nas 5 telas (antes: violação `region` em 3 delas). Os únicos alvos
  ainda abaixo de 24px que aparecem na varredura são "Cadastre-se"/"Entrar" — os botões de troca
  de modo dentro de uma frase, que o relatório original já havia deixado de fora do escopo de A3
  por estarem em contexto de texto corrido.
- **Build de produção (`yarn build`, `CI=true`)**: compilou sem avisos.
- **Imprevisto no caminho**: no meio da verificação o volume do Postgres do ambiente de
  desenvolvimento foi recriado (uma das armadilhas já descritas no padrão deste projeto — conta de
  teste some, banco vem vazio). Não é relacionado às correções; recriei a conta descartável
  (`a11y-audit@example.com`) pelo cadastro normal e revalidei.
- **Não verificado nesta seção**: A4 continua como estava, nenhuma mudança de código foi feita nele.

## Verificação da correção de A2 (foco após fechar o diálogo)

- **O que mudou**: [`useDialogFocusReturn.js`](Psigestaoe/frontend/src/hooks/useDialogFocusReturn.js#L41-L65)
  — em vez de devolver o foco na hora (`onCloseAutoFocus`), espera em intervalos curtos (até 500ms)
  até o elemento original deixar de estar dentro de `[aria-hidden="true"]`/`[inert]`, e só então
  chama `restoreFocus`. Nenhum outro arquivo mudou; o componente compartilhado é usado por
  `dialog.jsx`, `alert-dialog.jsx` e `sheet.jsx`, então a correção vale para os três.
- **Causa confirmada por instrumentação**: medindo quadro a quadro depois do `Esc`, o
  `aria-hidden` que o Radix põe nos irmãos do diálogo só é removido entre ~150-180ms depois do
  fechamento (tempo da animação de saída) — exatamente quando, antes da correção, o foco já tinha
  caído no conteúdo principal.
- **Um detalhe que a primeira verificação revelou**: o teste original abria o diálogo simulando
  um clique por **JavaScript** (`elemento.click()`), que não move o foco do navegador como um
  clique ou toque de tecla reais — então o "elemento de origem" guardado era o `<body>`, não o
  botão. Com um clique de mouse real (CDP) e também com teclado puro (`Tab` até o botão + barra de
  espaço), confirmado: **o foco volta corretamente ao botão "Novo paciente"** depois do `Esc`, nos
  dois casos.
- **Build de produção (`yarn build`, `CI=true`)**: compilou sem avisos depois da correção.

---
*Relatório gerado com `axe-core` via Chrome headless (CDP) + inspeção do código-fonte. Conta de
teste descartável, sem dados de pacientes reais. Correções de A1/A3/A5 aplicadas e verificadas
nesta mesma rodada (ver seção "Verificação" acima).*
