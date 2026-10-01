# legal.py
# ---------------------------------------------------------------------------
# Política de Privacidade do PsiGestão (versionada).
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

# Mude a versão sempre que o conteúdo mudar de forma relevante.
PRIVACY_POLICY_VERSION = "2026-09-30"
PRIVACY_POLICY_UPDATED_AT = "30/09/2026"

_PLACEHOLDER = "[A PREENCHER]"


def _controller() -> dict:
    """Dados de quem opera o PsiGestão, vindos do ambiente (variam por implantação)."""
    return {
        "name": os.environ.get("PRIVACY_CONTROLLER_NAME", "").strip() or _PLACEHOLDER,
        "document": os.environ.get("PRIVACY_CONTROLLER_DOCUMENT", "").strip() or _PLACEHOLDER,
        "dpo_name": os.environ.get("PRIVACY_DPO_NAME", "").strip() or _PLACEHOLDER,
        "dpo_email": os.environ.get("PRIVACY_DPO_EMAIL", "").strip() or _PLACEHOLDER,
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
            "Pacientes (inseridos pelo profissional ou pela integração com Google Forms que ele configurar): "
            "identificação (nome, CPF, RG, data de nascimento e idade), contato (telefone, e-mail, endereço e "
            "contato de emergência), escolaridade, estado civil, profissão, registro do consentimento, agenda "
            "de sessões e prontuário psicológico (evolução clínica e hipóteses diagnósticas). Dados de saúde "
            "são dados pessoais sensíveis (art. 5º, II, da LGPD) e recebem as proteções descritas no item 7.",
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
            "Fornecedores de infraestrutura (hospedagem e banco de dados), contratados como suboperadores, "
            "com obrigação de confidencialidade e segurança.",
            "Google: somente quando o profissional usa o login com Google (autenticação) ou a integração com "
            "Google Forms (os dados vão do formulário do profissional para o PsiGestão). As fontes da interface "
            "são carregadas do Google Fonts, o que expõe ao Google o endereço IP do navegador.",
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
        ("8. Por quanto tempo guardamos", [
            "Conta do profissional: enquanto estiver ativa. Depois do encerramento os dados são eliminados, "
            "exceto os que a lei obriga a guardar.",
            "Prontuários e registros documentais: no mínimo 5 anos contados do último registro (Resolução CFP "
            "nº 001/2009). O sistema impede a exclusão definitiva antes desse prazo; nesse período o paciente "
            "pode ser arquivado.",
            "Registros de acesso: no mínimo 6 meses (Marco Civil da Internet).",
            "Trilha de auditoria: enquanto houver dados relacionados sob guarda.",
        ]),
        ("9. Direitos dos titulares", [
            "A LGPD (art. 18) garante: confirmação da existência de tratamento; acesso aos dados; correção de "
            "dados incompletos, inexatos ou desatualizados; anonimização, bloqueio ou eliminação de dados "
            "desnecessários, excessivos ou tratados em desconformidade com a lei; portabilidade; eliminação dos "
            "dados tratados com base no consentimento; informação sobre com quem os dados são compartilhados; "
            "informação sobre a possibilidade de não consentir e suas consequências; e revogação do "
            "consentimento.",
            "Profissionais exercem esses direitos pelo próprio sistema (Privacidade & LGPD) ou pelo contato do "
            "encarregado indicado no item 11.",
            "Pacientes devem fazer o pedido diretamente ao seu psicólogo ou psicóloga, que é o controlador dos "
            "seus dados. O sistema oferece ao profissional as ferramentas para atender: exportação do "
            "prontuário (PDF/JSON), correção cadastral, anonimização e registro ou revogação do consentimento. "
            "Pedidos de eliminação de prontuário podem ser recusados enquanto durar a guarda obrigatória "
            "(art. 16, I, da LGPD).",
        ]),
        ("10. Cookies", [
            "Usamos apenas um cookie estritamente necessário (access_token), do tipo httpOnly, que mantém você "
            "conectado e não pode ser lido por scripts da página. Não usamos cookies de publicidade nem de "
            "rastreamento. O login com Google pode gravar cookies próprios do Google, regidos pela política "
            "de privacidade do Google.",
        ]),
        ("11. Encarregado e contato", [
            f"Encarregado pelo tratamento de dados pessoais: {c['dpo_name']} – {c['dpo_email']}.",
            "Você também pode apresentar reclamação à Autoridade Nacional de Proteção de Dados (ANPD), em "
            "www.gov.br/anpd.",
        ]),
        ("12. Alterações desta política", [
            "Quando esta política mudar de forma relevante, a versão é atualizada e os profissionais precisam "
            "aceitá-la novamente para continuar usando o sistema. O aceite fica registrado com data, hora e "
            f"endereço IP. Versão vigente: {PRIVACY_POLICY_VERSION}.",
        ]),
    ]
    return {
        "version": PRIVACY_POLICY_VERSION,
        "updated_at": PRIVACY_POLICY_UPDATED_AT,
        "controller": c,
        "sections": [{"title": title, "paragraphs": paragraphs} for title, paragraphs in sections],
    }
