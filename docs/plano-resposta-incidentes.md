# Plano de resposta a incidentes de segurança — PsiGestão

> **Rascunho (varredura, itens L3/E1).** Cobre o procedimento técnico e
> operacional. **Não substitui advogado**: antes de valer para clientes
> pagantes, peça a um advogado especializado em LGPD para revisar — em
> especial os prazos legais e o texto de comunicação a titulares, que têm
> implicações jurídicas diretas. Mantenha este arquivo atualizado quando a
> infraestrutura real de produção for decidida (hospedagem, monitoramento).

## 1. Quando este plano se aplica

Qualquer evento que possa ter exposto, alterado sem autorização ou tornado
indisponível dado pessoal de pacientes ou profissionais no PsiGestão. Exemplos:

- acesso ao banco de dados ou aos backups por quem não deveria;
- vazamento de uma chave (`JWT_SECRET`, `FERNET_KEY`, `SEARCH_INDEX_KEY`,
  senha do banco, senha do restic) — ver `scripts/secret_history_check.py`
  no skill de segurança para saber se um segredo antigo ainda está em uso;
- uma vulnerabilidade explorada (ex.: IDOR, CSRF, SQL/XSS) com evidência de uso real;
- perda de acesso aos dados sem backup válido (ex.: `FERNET_KEY` perdida);
- erro de configuração que expõe dados por engano (ex.: porta do banco publicada,
  CORS mal configurado, bucket de backup público).

Suspeita não confirmada também entra aqui — a avaliação de risco (seção 3)
é o que decide os próximos passos, não a certeza inicial.

## 2. Quem decide e como contatar

Hoje a lista de quem administra o sistema é a mesma de `ADMIN_EMAILS`
(`backend/.env`). Preencha quando houver mais de uma pessoa:

| Papel | Nome | Contato | Responsabilidade |
|---|---|---|---|
| Responsável técnico | _preencher_ | _preencher_ | Conter o incidente, avaliar o alcance técnico |
| Encarregado (DPO) | `PRIVACY_DPO_NAME` / `PRIVACY_DPO_EMAIL` (`.env`) | | Avaliar risco ao titular, decidir comunicação, falar com a ANPD |
| Controlador (quem opera a conta afetada) | — | pelo e-mail da conta | Em incidentes que afetam só uma conta de profissional, ela precisa ser avisada: é a controladora dos dados dos próprios pacientes |

Qualquer pessoa da equipe que suspeitar de um incidente avisa o responsável
técnico e o encarregado **imediatamente**, mesmo fora do horário — não espera
confirmação total antes de avisar.

## 3. Avaliação de risco (primeiras 24h)

Responda, com o que já se sabe (atualiza conforme a investigação avança):

1. **O que foi exposto/alterado?** Quais tabelas, quais campos, quantas contas.
   Campos criptografados (nome, CPF, RG, contato, prontuário — ver
   `crypto.py`/`ENCRYPTED_COLUMNS` em `rotate_encryption_key.py`) só são dado
   legível se a `FERNET_KEY` também foi comprometida; registre isso.
2. **Quem é afetado?** Número de contas de profissionais e, por elas, de pacientes.
3. **Há dado sensível de saúde envolvido (art. 11)?** No PsiGestão, quase
   sempre sim (prontuário, diagnóstico) — isso eleva a severidade por padrão.
4. **O incidente ainda está em andamento?** (acesso contínuo, chave ainda
   válida, backup ainda exposto) — define a urgência da contenção (seção 4).
5. **Risco concreto ao titular**: constrangimento, discriminação, dano
   financeiro, dano à saúde mental (ex.: prontuário de terapia exposto).

Com riscos relevantes ao titular confirmados (passo 5), os prazos da seção 5
começam a contar a partir do momento em que o incidente foi **confirmado**
(não da suspeita inicial) — registre a data/hora exata dessa confirmação.

## 4. Conter (o mais rápido possível)

Ordem sugerida, adaptando ao caso real:

1. **Trocar os segredos** potencialmente comprometidos: `JWT_SECRET` (encerra
   todas as sessões na hora), senha do banco, `SEARCH_INDEX_KEY`
   (`python rebuild_search_index.py --all` depois). A `FERNET_KEY` só se troca
   com o script de rotação (`rotate_encryption_key.py`) — nunca às pressas sem
   backup do banco antes.
2. **Revogar o acesso** que causou o incidente: credencial de servidor, chave
   de API de um fornecedor, conta comprometida (`end_login_sessions`,
   suspender a conta pelo painel de administração).
3. **Isolar** se necessário: desligar a rota/serviço afetado, restringir rede.
4. Registrar **linha do tempo** (o que se sabe, quando, por quem foi feito cada
   passo de contenção) — vira a base do relatório à ANPD, se for o caso.

## 5. Comunicar

### À ANPD e aos titulares — prazo de **3 dias úteis**

Resolução CD/ANPD nº 15/2024: incidente com risco relevante aos titulares
exige comunicação à ANPD em até 3 dias úteis da **confirmação** (não da
suspeita). A comunicação inclui, no mínimo:

- descrição do incidente (natureza, dados afetados, data e duração);
- dados afetados (tipos, inclusive se são sensíveis — saúde, art. 11);
- medidas técnicas de segurança já em vigor (criptografia, trilha de
  auditoria, controle de acesso — ver `seguranca-backend.md`);
- riscos para os titulares;
- medidas tomadas para conter e remediar;
- providências para reverter ou mitigar os efeitos.

A pessoa titular (profissional e, por meio dela, os pacientes) também é
avisada quando houver risco relevante — pelo e-mail já cadastrado, em
linguagem simples, sem jargão técnico, nunca citando dado de outro titular.

### Interno (equipe)

Antes ou junto da comunicação externa: avisar quem administra o sistema
(seção 2) e registrar o caso (seção 6).

## 6. Registrar

Guarde, para cada incidente (mesmo os de baixo risco, descartados após a
avaliação da seção 3): data da detecção, data da confirmação, descrição,
dados afetados, ações de contenção, se houve comunicação à ANPD/titulares (e
quando), e a lição aprendida (o que muda no sistema ou no processo para não
repetir). **Nunca** inclua dado de paciente (nome, CPF, conteúdo clínico)
neste registro — identifique por ID, como a trilha de auditoria do próprio
sistema já faz.

## 7. Depois do incidente

- Corrigir a causa raiz (não só o sintoma) — abrir o trabalho como qualquer
  outra tarefa de segurança, com teste que comprove a correção
  (`seguranca-backend.md`, item 20).
- Revisar este plano: o que funcionou, o que faltou.
- Se o incidente veio de um fornecedor (suboperador — hospedagem, e-mail,
  monitoramento), confirmar que ele também cumpriu a parte dele.

## Pendências (fora do alcance deste rascunho)

- **RIPD** (Relatório de Impacto à Proteção de Dados, art. 38): recomendado
  para dados sensíveis em escala; ainda não existe um documento formal.
- **Registro das operações de tratamento** (art. 37; formato simplificado,
  Resolução CD/ANPD nº 2/2022): ainda não existe.
- **Contrato/Termo de operador formalizado com cada profissional**: a base
  textual já existe nos Termos de Uso (`backend/legal.py`, cláusulas de
  suboperadores, aviso de incidente, devolução/eliminação de dados), mas o
  texto geral **precisa de revisão por advogado** antes de valer para clientes
  pagantes (o próprio README já sinaliza isso).
- Este plano assume **uma pessoa ou equipe pequena** administrando o sistema
  (o cenário atual, pré-expansão); revise os papéis da seção 2 ao crescer a
  equipe.
