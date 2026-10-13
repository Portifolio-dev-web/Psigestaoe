# legal.py
# ---------------------------------------------------------------------------
# Política de Privacidade e Termos de Uso do PsiGestão (versionados JUNTOS).
#
# Análise da administração (AD1): até a versão 2026-10-09 só existia a
# política. Quem pagava e era suspensa não tinha como saber QUANDO a suspensão
# podia acontecer, nem que a administração vê dados da conta. Agora:
#   - os Termos de Uso (terms_of_use) trazem as regras do serviço, inclusive as
#     cláusulas de operador da LGPD (art. 39), os planos e a suspensão;
#   - os dois documentos têm a MESMA versão (PRIVACY_POLICY_VERSION) e um único
#     aceite cobre os dois (POST /auth/accept-terms, com data e IP na trilha).
#
# POR QUE ESTE ARQUIVO EXISTE:
#   O cadastro pedia "Li e aceito os Termos de Privacidade", mas não existia
#   documento nenhum: o usuário "aceitava" um texto que não podia ler, e o
#   login com Google marcava o aceite sozinho (item 16 da auditoria). A LGPD
#   (art. 9º) exige informação clara e acessível sobre o tratamento, e o
#   aceite só vale como evidência se soubermos QUAL texto foi aceito.
#
# COMO FUNCIONA:
#   - Este módulo é a ÚNICA fonte do texto e da versão. O frontend busca o
#     texto em GET /api/legal/privacy-policy, e o backend compara a versão
#     aceita por cada usuário com PRIVACY_POLICY_VERSION.
#   - Ao mudar o conteúdo de forma relevante, ALTERE PRIVACY_POLICY_VERSION
#     (use a data da mudança). Todos os profissionais serão levados à tela de
#     aceite no próximo acesso, e o novo aceite fica registrado com data e IP.
#
# ATENÇÃO ANTES DO DEPLOY:
#   - Este texto é um MODELO técnico, fiel ao que o sistema faz hoje. Ele deve
#     ser revisado por um advogado com experiência em LGPD antes da publicação.
#   - Preencha os dados do controlador pelas variáveis de ambiente abaixo.
#     Em produção, o servidor registra um alerta enquanto houver campos vazios.
# ---------------------------------------------------------------------------
import logging
import os

logger = logging.getLogger("psigestao.legal")

# Mude a versão sempre que o conteúdo de QUALQUER dos dois documentos mudar de
# forma relevante (todos aceitam de novo no próximo acesso).
# 2026-10-09: cadastro pelo próprio paciente (link temporário) no lugar do Google
# Forms; e correções de exatidão (as fontes não vêm mais do Google; nome real do cookie).
# 2026-10-11: Termos de Uso (documento novo); administração da plataforma e
# suspensão em modo restrito (análise da administração, AD1/AD2); dados da
# assinatura; e a lista completa de fornecedores (e-mail, cópias de segurança,
# monitoramento de erros e meio de pagamento).
# 2026-10-13: na suspensão a senha é desativada e a nova é criada pelo link do
# e-mail (revisão de segurança, RS1); o modo restrito não troca senha nem
# desativa as duas etapas; e o cadastro incompleto tem prazo de 60 dias (RS2).
PRIVACY_POLICY_VERSION = "2026-10-13"
PRIVACY_POLICY_UPDATED_AT = "13/10/2026"

# SEGURANÇA/LGPD (varredura, item 12): nome e CPF reais tinham sido escritos
# aqui e no texto da seção 1. Este arquivo vai para o git e o texto é público
# (GET /api/legal/privacy-policy não exige login), então o CPF ficava exposto a
# qualquer pessoa. Além disso, sem as variáveis de ambiente, o nome aparecia
# também no lugar do documento e do e-mail do encarregado.
# Regra: dados reais do controlador e do encarregado vêm SÓ das variáveis
# PRIVACY_* (fora do git). Aqui fica apenas o marcador neutro, que o
# warn_if_incomplete() usa para detectar campos não preenchidos.
_PLACEHOLDER = "[A PREENCHER]"


def _controller() -> dict:
    """Dados de quem opera o PsiGestão, vindos do ambiente (variam por implantação)."""
    return {
        "name": os.environ.get("PRIVACY_CONTROLLER_NAME", "").strip() or _PLACEHOLDER,
        "document": os.environ.get("PRIVACY_CONTROLLER_DOCUMENT", "").strip() or _PLACEHOLDER,
        "dpo_name": os.environ.get("PRIVACY_DPO_NAME", "").strip() or _PLACEHOLDER,
        "dpo_email": os.environ.get("PRIVACY_DPO_EMAIL", "").strip() or _PLACEHOLDER,
    }


def contact_info() -> dict:
    """Análise de UI (O2): contatos mostrados na página de Ajuda.

    Antes não havia "Ajuda" nem contato de suporte em nenhuma tela, e o contato
    do encarregado (LGPD, art. 41) só aparecia dentro da política.
    - support_email: SUPPORT_EMAIL (opcional; dúvidas de uso e problemas de acesso);
    - dpo_*: o encarregado, os mesmos dados da política (PRIVACY_DPO_*).
    Campo não configurado volta vazio, e a tela não mostra a linha (em vez do
    marcador "[A PREENCHER]", que é para a política acusar a falta)."""
    c = _controller()
    clean = lambda value: "" if value == _PLACEHOLDER else value  # noqa: E731
    return {
        "support_email": os.environ.get("SUPPORT_EMAIL", "").strip(),
        "dpo_name": clean(c["dpo_name"]),
        "dpo_email": clean(c["dpo_email"]),
    }


def warn_if_incomplete(environment: str) -> None:
    """A LGPD (art. 41) exige divulgar a identidade e o contato do encarregado.
    Publicar a política com campos vazios em produção é descumprimento."""
    missing = [key for key, value in _controller().items() if value == _PLACEHOLDER]
    if missing and environment == "production":
        logger.critical(
            "LGPD: Política de Privacidade publicada com dados do controlador em branco (%s). "
            "Defina PRIVACY_CONTROLLER_NAME, PRIVACY_CONTROLLER_DOCUMENT, PRIVACY_DPO_NAME e PRIVACY_DPO_EMAIL.",
            ", ".join(missing),
        )


def privacy_policy() -> dict:
    c = _controller()
    sections = [
        ("1. Quem somos", [
            f"O PsiGestão é um sistema de gestão clínica e prontuário eletrônico para profissionais de "
            f"psicologia, operado por {c['name']} (CNPJ/CPF {c['document']}).",
            "Esta política explica quais dados pessoais tratamos, por que, com quem compartilhamos, por "
            "quanto tempo guardamos e como você exerce seus direitos, conforme a Lei Geral de Proteção de "
            "Dados Pessoais (Lei nº 13.709/2018 – LGPD).",
            # AD1: a política e os Termos de Uso andam juntos (mesma versão, um aceite).
            "As regras de uso do serviço (planos, uso permitido, suspensão e encerramento da conta) estão nos "
            "Termos de Uso. Os dois documentos têm a mesma versão e são aceitos juntos.",
        ]),
        ("2. Papel de cada parte", [
            "Dados dos profissionais cadastrados (nome, e-mail, CRP e dados de acesso): o PsiGestão é o "
            "controlador.",
            "Dados dos pacientes registrados pelo profissional (cadastro, agenda e prontuário): o profissional "
            "é o controlador, porque é ele quem decide o que registrar e para quê. O PsiGestão atua como "
            "operador, tratando esses dados apenas para prestar o serviço e conforme as instruções do "
            "profissional (art. 39 da LGPD).",
        ]),
        ("3. Dados que tratamos", [
            "Profissionais: nome, e-mail, número de CRP, senha (guardada apenas como hash bcrypt, nunca em "
            "texto), nome e foto do perfil Google (somente se usar o login com Google), registros de acesso "
            "(endereço IP, data, hora e navegador) e a trilha de auditoria das ações realizadas no sistema.",
            # AD1 (exatidão): a assinatura existe desde 2026-10-08 e não estava na política.
            "Assinatura do profissional: plano, situação (teste grátis, assinante, atraso, somente leitura, "
            "cortesia ou suspensa), datas de pagamento e os identificadores do meio de pagamento. Os dados "
            "do cartão ficam apenas com o meio de pagamento; o PsiGestão não os recebe.",
            "Pacientes (inseridos pelo profissional ou pelo próprio paciente, num link de cadastro "
            "temporário que o profissional envia): "
            "identificação (nome, CPF, RG, data de nascimento e idade), contato (telefone, e-mail, endereço e "
            "contato de emergência), escolaridade, estado civil, profissão, registro do consentimento, agenda "
            "de sessões e prontuário psicológico (evolução clínica e hipóteses diagnósticas). Dados de saúde "
            "são dados pessoais sensíveis (art. 5º, II, da LGPD) e recebem as proteções descritas no item 7.",
            "Link de cadastro: vale 30 minutos para começar e, depois de começado, 10 minutos para preencher "
            "(prorrogáveis). Enquanto o paciente preenche, o que ele digita é guardado criptografado; se o "
            "tempo acabar antes do envio, o que foi preenchido chega ao profissional como cadastro incompleto, "
            "que fica disponível por até 60 dias para ele completar com o paciente. "
            "A data, a hora e o endereço IP do envio são registrados como evidência do consentimento.",
        ]),
        ("4. Finalidades e bases legais", [
            "Criar e manter a conta do profissional e prestar o serviço contratado: execução de contrato "
            "(art. 7º, V).",
            "Registro documental dos atendimentos e guarda do prontuário psicológico: cumprimento de "
            "obrigação legal e regulatória (art. 7º, II, e art. 11, II, \"a\"), em especial a Resolução CFP "
            "nº 001/2009, e tutela da saúde em procedimento realizado por profissional de saúde "
            "(art. 11, II, \"f\").",
            "Guarda dos registros de acesso por 6 meses: obrigação legal do Marco Civil da Internet "
            "(Lei nº 12.965/2014, art. 15).",
            "Trilha de auditoria e segurança da conta (prevenção a fraudes e acessos indevidos): obrigação de "
            "registrar as operações de tratamento e de adotar medidas de segurança (arts. 37 e 46 da LGPD).",
            "Não usamos dados de pacientes para publicidade, criação de perfis ou treinamento de modelos de "
            "inteligência artificial, e não vendemos dados pessoais.",
        ]),
        ("5. Compartilhamento", [
            # AD1 (exatidão, LGPD art. 6º, VI): a lista citava só hospedagem e banco de dados.
            "Fornecedores contratados como suboperadores, com obrigação de confidencialidade e segurança: "
            "hospedagem e banco de dados; envio de e-mail (confirmação de cadastro, troca de senha e avisos da "
            "conta, sem dados de pacientes); armazenamento das cópias de segurança (criptografadas); e "
            "monitoramento de erros do sistema, quando ativado (sem dados de pacientes).",
            "Meio de pagamento: recebe os dados necessários à cobrança da assinatura do profissional (nome, "
            "e-mail e os dados de pagamento que ele digita na página do próprio meio de pagamento).",
            "Google: somente quando o profissional usa o login com Google (autenticação). A página de cadastro "
            "do paciente não carrega nada do Google, e as fontes da interface são servidas pelo próprio "
            "PsiGestão.",
            "Autoridades públicas, somente mediante requisição legal ou ordem judicial.",
            "Não há nenhum outro compartilhamento.",
        ]),
        ("6. Transferência internacional", [
            "Alguns fornecedores, como o Google, podem tratar dados fora do Brasil. Nesses casos a "
            "transferência segue o art. 33 da LGPD, com fornecedores que oferecem garantias contratuais de "
            "proteção de dados compatíveis com a lei brasileira.",
        ]),
        ("7. Segurança", [
            "Em trânsito: todas as conexões usam HTTPS/TLS.",
            "Em repouso: os dados que identificam o paciente e o conteúdo clínico (nome, CPF, RG, contatos, "
            "endereço, observações, prontuários e agenda) são criptografados antes de chegar ao banco de dados, "
            "com o padrão Fernet (AES-128-CBC com autenticação HMAC-SHA256). A chave fica fora do banco.",
            "Senhas com hash bcrypt, sessões com expiração que são encerradas de verdade ao sair, e limite de "
            "tentativas de login.",
            "Trilha de auditoria somente de inclusão: registra quem visualizou, criou, alterou, exportou, "
            "arquivou ou excluiu dados, com data, hora e endereço IP, e não pode ser alterada pelo sistema.",
            "Em caso de incidente de segurança que possa causar risco ou dano relevante, comunicaremos os "
            "profissionais afetados e a Autoridade Nacional de Proteção de Dados (ANPD), nos termos do "
            "art. 48 da LGPD.",
        ]),
        # AD1: o que a administração vê e pode fazer (antes a política não dizia).
        ("8. Administração da plataforma", [
            "A equipe que administra o PsiGestão (contas definidas na configuração do servidor, com verificação "
            "em duas etapas obrigatória) vê, de cada conta de profissional: nome, e-mail, data de cadastro, "
            "último acesso, situação da assinatura, se usa a verificação em duas etapas e a QUANTIDADE de "
            "pacientes cadastrados. A administração não vê nenhum dado de paciente (cadastro, agenda ou "
            "prontuário).",
            "A administração pode conceder ou retirar acesso de cortesia, prorrogar o teste grátis, encerrar as "
            "sessões da conta em todos os aparelhos e suspender ou reativar a conta, nas hipóteses dos Termos "
            "de Uso. Cada ação exige a confirmação da senha de quem administra, fica registrada na trilha de "
            "auditoria da conta afetada, e o profissional recebe um e-mail explicando o que mudou e por quê.",
            "Conta suspensa: a senha é desativada e um link para criar outra é enviado ao e-mail da conta, para "
            "que só a titular volte a entrar. Depois disso, o profissional entra apenas para exportar os seus "
            "dados e os dos seus pacientes, reforçar a segurança da conta e encerrá-la. Nenhum dado é alterado ou "
            "apagado por causa da suspensão.",
            "Bases legais: execução do contrato (art. 7º, V), prevenção a fraudes e segurança do serviço "
            "(art. 7º, IX, e art. 46).",
        ]),
        ("9. Por quanto tempo guardamos", [
            "Conta do profissional: enquanto estiver ativa. Depois do encerramento os dados são eliminados, "
            "exceto os que a lei obriga a guardar.",
            "Prontuários e registros documentais: no mínimo 5 anos contados do último registro (Resolução CFP "
            "nº 001/2009). O sistema impede a exclusão definitiva antes desse prazo; nesse período o paciente "
            "pode ser arquivado.",
            "Registros de acesso: no mínimo 6 meses (Marco Civil da Internet).",
            "Trilha de auditoria: enquanto houver dados relacionados sob guarda.",
            "Conta suspensa: os dados continuam guardados, sem alteração, durante toda a suspensão.",
            # RS2: prazo do cadastro incompleto.
            "Cadastro incompleto (link de cadastro em que o tempo acabou antes do envio): até 60 dias depois que o "
            "tempo acabou. O profissional recebe um lembrete por e-mail e um aviso 7 dias antes; se não completar "
            "nem descartar, o que o paciente digitou é eliminado automaticamente.",
            # AD1 (exatidão): backups guardam o que já foi excluído até serem substituídos.
            "Cópias de segurança (backup): são criptografadas e substituídas periodicamente; um dado excluído "
            "continua nas cópias mais antigas até elas serem descartadas.",
        ]),
        ("10. Direitos dos titulares", [
            "A LGPD (art. 18) garante: confirmação da existência de tratamento; acesso aos dados; correção de "
            "dados incompletos, inexatos ou desatualizados; anonimização, bloqueio ou eliminação de dados "
            "desnecessários, excessivos ou tratados em desconformidade com a lei; portabilidade; eliminação dos "
            "dados tratados com base no consentimento; informação sobre com quem os dados são compartilhados; "
            "informação sobre a possibilidade de não consentir e suas consequências; e revogação do "
            "consentimento.",
            # Só o nome do menu mudou (ajuste de redação, sem mudança de conteúdo):
            # a versão da política continua a mesma e ninguém precisa aceitar de novo.
            "Profissionais exercem esses direitos pelo próprio sistema (menu Configurações) ou pelo contato do "
            "encarregado indicado no item 12. Com a conta suspensa, a exportação dos dados e o encerramento "
            "da conta continuam disponíveis.",
            "Pacientes devem fazer o pedido diretamente ao seu psicólogo ou psicóloga, que é o controlador dos "
            "seus dados. O sistema oferece ao profissional as ferramentas para atender: exportação do "
            "prontuário (PDF/JSON), correção cadastral, anonimização e registro ou revogação do consentimento. "
            "Pedidos de eliminação de prontuário podem ser recusados enquanto durar a guarda obrigatória "
            "(art. 16, I, da LGPD).",
        ]),
        ("11. Cookies", [
            "Usamos apenas um cookie estritamente necessário (__Host-access_token), do tipo httpOnly, que mantém você "
            "conectado e não pode ser lido por scripts da página. Não usamos cookies de publicidade nem de "
            "rastreamento. O login com Google pode gravar cookies próprios do Google, regidos pela política "
            "de privacidade do Google.",
        ]),
        ("12. Encarregado e contato", [
            f"Encarregado pelo tratamento de dados pessoais: {c['dpo_name']} – {c['dpo_email']}.",
            "Você também pode apresentar reclamação à Autoridade Nacional de Proteção de Dados (ANPD), em "
            "www.gov.br/anpd.",
        ]),
        ("13. Alterações desta política", [
            "Quando esta política ou os Termos de Uso mudarem de forma relevante, a versão é atualizada e os "
            "profissionais precisam aceitá-los novamente para continuar usando o sistema. O aceite fica "
            f"registrado com data, hora e endereço IP. Versão vigente: {PRIVACY_POLICY_VERSION}.",
        ]),
    ]
    return {
        "version": PRIVACY_POLICY_VERSION,
        "updated_at": PRIVACY_POLICY_UPDATED_AT,
        "controller": c,
        "sections": [{"title": title, "paragraphs": paragraphs} for title, paragraphs in sections],
    }


def _brl(cents: int) -> str:
    """59000 -> "R$ 590,00" (formato brasileiro)."""
    return "R$ " + f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------------------
# TERMOS DE USO (análise da administração, AD1)
#
# ATENÇÃO ANTES DO DEPLOY: como a política, é um MODELO técnico fiel ao que o
# sistema faz hoje (cada regra abaixo existe no código: billing.py, server.py).
# Deve ser revisado por um advogado (LGPD e direito do consumidor) antes de
# valer para clientes pagantes. Não prometa aqui o que o sistema não faz.
# Prazos e preços vêm da configuração (billing.py), para o texto nunca divergir
# do que a tela cobra.
# ---------------------------------------------------------------------------
def terms_of_use() -> dict:
    import billing  # aqui dentro: legal.py é importado cedo pelo server.py

    c = _controller()
    sections = [
        ("1. O serviço", [
            f"O PsiGestão, operado por {c['name']} (CNPJ/CPF {c['document']}), é um sistema on-line de agenda, "
            "cadastro de pacientes e prontuário psicológico para profissionais de psicologia. Ao criar a conta "
            "e aceitar estes Termos, você contrata o uso do sistema nas condições abaixo.",
            "O PsiGestão não presta serviços de psicologia: o atendimento, as decisões clínicas e o conteúdo do "
            "prontuário são de responsabilidade exclusiva do profissional.",
        ]),
        ("2. Quem pode usar", [
            "Psicólogas e psicólogos com inscrição ativa no Conselho Regional de Psicologia. Você declara que os "
            "dados da conta (nome, e-mail e CRP) são verdadeiros e os mantém atualizados.",
            "A conta é pessoal e intransferível: não compartilhe o acesso com outras pessoas, nem com a equipe "
            "do consultório.",
        ]),
        ("3. Sua conta e a segurança", [
            "Você é responsável por guardar a sua senha e o acesso ao e-mail da conta. Recomendamos ativar a "
            "verificação em duas etapas (Configurações > Minha conta).",
            "A trilha de auditoria (Configurações > Auditoria) mostra os acessos e as ações feitas na conta. "
            "Se suspeitar de acesso indevido, troque a senha, use \"Sair dos outros dispositivos\" e avise o "
            "suporte.",
        ]),
        ("4. Dados dos seus pacientes (o PsiGestão como operador)", [
            "Você é a controladora dos dados dos seus pacientes: decide o que registrar e para quê, responde "
            "pela base legal do tratamento, pelo sigilo profissional (Código de Ética Profissional do Psicólogo) "
            "e pelo atendimento aos pedidos dos pacientes. O PsiGestão é operador (LGPD, art. 39): trata esses "
            "dados apenas para prestar o serviço e conforme as suas instruções, que são as ações que você faz "
            "no sistema.",
            "O PsiGestão não usa os dados dos pacientes para nenhuma outra finalidade, não os vende e não os "
            "compartilha, exceto com os fornecedores necessários ao serviço (suboperadores, listados na Política "
            "de Privacidade), sob obrigação de confidencialidade e segurança, ou por ordem judicial ou "
            "requisição legal.",
            "Incidentes de segurança que possam afetar dados dos seus pacientes são comunicados a você sem demora, "
            "com as informações necessárias para você avaliar o caso e, se preciso, comunicar a Autoridade "
            "Nacional de Proteção de Dados e os pacientes (LGPD, art. 48).",
            "Você pode exportar todos os dados (PDF e JSON) a qualquer momento, inclusive com a conta em somente "
            "leitura ou suspensa. Ao fim do contrato, ao encerrar a conta, os dados são eliminados, exceto os "
            "prontuários sob guarda obrigatória, que ficam criptografados e sem acesso até o fim do prazo "
            "(Resolução CFP nº 001/2009) e então são eliminados automaticamente.",
        ]),
        ("5. Planos, teste grátis e pagamento", [
            f"Toda conta nova tem {billing.TRIAL_DAYS} dias de teste grátis, sem cartão. Depois, o uso completo "
            f"depende de um plano: mensal ({_brl(billing.PRICES['monthly'])}) ou anual "
            f"({_brl(billing.PRICES['annual'])}), pelos valores exibidos no sistema no momento da contratação. "
            "Mudanças de preço são avisadas com antecedência por e-mail e só valem a partir da renovação seguinte.",
            "O pagamento é processado pelo meio de pagamento contratado pelo PsiGestão; os dados do cartão ficam "
            "apenas com ele.",
            f"Atraso: se a renovação não for paga, tudo continua funcionando por {billing.GRACE_DAYS} dias de "
            "tolerância. Depois, a conta passa a somente leitura: você continua vendo e exportando tudo, "
            "corrigindo cadastros e atendendo os pedidos dos pacientes, mas não registra atendimentos novos. "
            "A falta de pagamento nunca leva à suspensão nem à perda de dados.",
            "Cancelamento: a qualquer momento, em Configurações > Assinatura, sem multa; o acesso completo vale "
            "até o fim do período já pago. O direito de arrependimento do Código de Defesa do Consumidor "
            "(art. 49) é respeitado quando aplicável.",
            "Cortesia: a administração pode conceder acesso sem cobrança a contas específicas, por tempo "
            "indeterminado, e retirá-lo com aviso por e-mail; ao ser retirada, valem as regras normais dos planos.",
        ]),
        ("6. Uso permitido", [
            "O sistema deve ser usado apenas para a gestão da sua prática profissional em psicologia. É proibido: "
            "registrar dados de pessoas sem base legal; usar a conta de outra pessoa ou deixar que outra pessoa "
            "use a sua; tentar acessar dados de outras contas, burlar limites ou testar a segurança do sistema sem "
            "autorização; inserir conteúdo ilegal; e fraudar pagamentos ou os dados da conta.",
        ]),
        ("7. Administração da plataforma", [
            "A administração do PsiGestão vê, de cada conta, apenas: nome, e-mail, datas de cadastro e de último "
            "acesso, situação da assinatura, se usa a verificação em duas etapas e a quantidade de pacientes. "
            "Nunca vê dados de pacientes.",
            "Ela pode conceder ou retirar cortesia, prorrogar o teste grátis, encerrar as sessões da conta em todos "
            "os aparelhos (por exemplo, em suspeita de acesso indevido; você entra de novo com a sua senha) e "
            "suspender ou reativar a conta (item 8). Toda ação fica na trilha de auditoria da sua conta, e você "
            "recebe um e-mail explicando o que mudou e por quê.",
        ]),
        ("8. Suspensão da conta", [
            "A conta só pode ser suspensa em caso de: (a) fraude, inclusive no pagamento ou nos dados da conta; "
            "(b) uso da conta por quem não é a titular; (c) violação grave do uso permitido (item 6); ou (d) "
            "ordem judicial ou determinação de autoridade competente. Atraso de pagamento não é motivo de "
            "suspensão (ver item 5).",
            "Você recebe um e-mail com o motivo, e a suspensão fica registrada na trilha de auditoria da conta. "
            "Por segurança, a senha da conta é desativada e o mesmo e-mail traz um link para criar outra: assim, só "
            "quem tem acesso ao e-mail da conta volta a entrar. Quem entra pelo Google continua entrando por ele.",
            "Durante a suspensão (modo restrito), você continua entrando no sistema, mas só para exportar os seus "
            "dados e os dos seus pacientes (para cumprir a guarda do prontuário e atender os pedidos dos "
            "pacientes), consultar a trilha de auditoria, reforçar a segurança da conta (verificação em duas "
            "etapas, aparelhos conectados) e encerrá-la. Não é possível registrar, agendar, editar, enviar links "
            "de cadastro de pacientes, trocar a senha por dentro do sistema nem desativar a verificação em duas "
            "etapas. Nenhum dado é alterado ou apagado por causa da suspensão.",
            "Você pode contestar a suspensão pelo contato do suporte (página Ajuda). A resposta é enviada por "
            "e-mail, com a decisão e o motivo. Superado o motivo, a conta é reativada e tudo volta a funcionar "
            "como antes, também com aviso por e-mail.",
        ]),
        ("9. Encerramento da conta", [
            "Você pode encerrar a conta quando quiser, em Configurações > Privacidade. Antes, exporte os dados que "
            "precisa guardar. O que acontece com os dados ao encerrar está no item 4 e na Política de Privacidade.",
        ]),
        ("10. Disponibilidade e cópias de segurança", [
            "Trabalhamos para manter o sistema disponível e fazemos cópias de segurança periódicas e "
            "criptografadas, mas não há garantia de funcionamento ininterrupto (manutenções e falhas de "
            "fornecedores podem acontecer). Recomendamos exportar os seus dados periodicamente.",
        ]),
        ("11. Responsabilidades", [
            "O PsiGestão responde pelas obrigações de operador e de segurança previstas na LGPD. O profissional "
            "responde pelo conteúdo que registra, pelas decisões clínicas e pelo uso da conta em desacordo com "
            "estes Termos.",
        ]),
        ("12. Alterações destes Termos", [
            "Mudanças relevantes geram uma nova versão, que você aceita no próximo acesso; o aceite fica registrado "
            f"com data, hora e endereço IP. Versão vigente: {PRIVACY_POLICY_VERSION}.",
        ]),
        ("13. Lei aplicável e contato", [
            "Estes Termos seguem as leis brasileiras. Dúvidas: pelo contato do suporte na página Ajuda, ou com o "
            f"encarregado de dados: {c['dpo_name']} – {c['dpo_email']}.",
        ]),
    ]
    return {
        "version": PRIVACY_POLICY_VERSION,
        "updated_at": PRIVACY_POLICY_UPDATED_AT,
        "controller": c,
        "sections": [{"title": title, "paragraphs": paragraphs} for title, paragraphs in sections],
    }
