# rotate_encryption_key.py
# ---------------------------------------------------------------------------
# Recriptografa todos os campos sensíveis com a chave atual (FERNET_KEY).
#
# Criado junto com a CORREÇÃO DE SEGURANÇA (item 2 da auditoria): os dados
# antigos foram gravados com uma chave derivada do JWT_SECRET. Este script
# lê esses dados com a chave antiga e grava de novo com a FERNET_KEY, para
# que o JWT_SECRET possa ser trocado sem perder prontuários.
#
# Chaves aceitas para LER (tentadas em ordem, a primeira que funcionar vence):
#   1. FERNET_KEY                        (chave nova; dado já migrado)
#   2. FERNET_OLD_KEYS                   (chaves antigas explícitas, separadas por vírgula)
#   3. derivada de LEGACY_JWT_SECRET     (ou de JWT_SECRET, se LEGACY_JWT_SECRET não existir)
#   4. chave fixa de desenvolvimento     (dados criados sem nenhuma chave configurada)
# Os dados são SEMPRE gravados com a FERNET_KEY.
#
# Uso (dentro do container da API):
#   python rotate_encryption_key.py            -> simulação: só mostra o que faria
#   python rotate_encryption_key.py --apply    -> grava as alterações
#
# Segurança da operação:
#   - Tudo roda em UMA transação. Se um único valor não puder ser lido com
#     nenhuma chave, nada é gravado (rollback) e o script lista o problema.
#   - É idempotente: rodar de novo depois de migrado não estraga nada.
#   - FAÇA BACKUP DO BANCO ANTES de usar --apply.
# ---------------------------------------------------------------------------
import argparse
import asyncio
import os
import sys

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select

from crypto import dev_fallback_key, is_fernet_token, legacy_key_from_secret
from database import AsyncSessionLocal
from models import EmailToken, Patient, Record, RecordVersion, Session, User

# Tabela -> colunas criptografadas. Precisa acompanhar os encrypt_field() do server.py.
#
# Item 13 da auditoria: a lista cresceu. Nome, telefone, e-mail, contato de
# emergência e observações do paciente, e título/nome/notas da agenda, antes
# ficavam em texto puro. Depois de atualizar o código, rode este script com
# --apply UMA vez: os valores antigos em texto puro são criptografados (o
# servidor continua lendo texto puro legado até lá, então nada quebra no meio).
ENCRYPTED_COLUMNS = {
    Patient: ("full_name", "cpf", "rg", "phone", "email", "address", "emergency_contact", "initial_notes"),
    Record: ("content", "diagnosis"),
    RecordVersion: ("content", "diagnosis"),
    Session: ("title", "patient_name", "notes"),
    # Produto: chave dos rascunhos do navegador (guardada criptografada no banco).
    # Segurança (backlog: MFA): segredo do aplicativo autenticador.
    User: ("draft_key", "mfa_secret"),
    # Segurança média: dados do cadastro ainda não confirmado (nome).
    EmailToken: ("payload",),
}


def build_keyring() -> MultiFernet:
    raw_new = os.environ.get("FERNET_KEY", "").strip()
    if not raw_new:
        sys.exit("ERRO: defina FERNET_KEY com a NOVA chave antes de migrar.")
    try:
        keys = [Fernet(raw_new.encode())]
    except ValueError:
        sys.exit("ERRO: FERNET_KEY inválida (precisa ser uma chave Fernet de 44 caracteres).")

    for raw in os.environ.get("FERNET_OLD_KEYS", "").split(","):
        if raw.strip():
            keys.append(Fernet(raw.strip().encode()))

    legacy_secret = os.environ.get("LEGACY_JWT_SECRET") or os.environ.get("JWT_SECRET")
    if legacy_secret:
        keys.append(Fernet(legacy_key_from_secret(legacy_secret)))

    keys.append(Fernet(dev_fallback_key()))
    return MultiFernet(keys)


async def migrate(apply: bool) -> None:
    keyring = build_keyring()
    rotated = plaintext = failed = 0

    async with AsyncSessionLocal() as db:
        for model, columns in ENCRYPTED_COLUMNS.items():
            rows = (await db.execute(select(model))).scalars().all()
            for row in rows:
                for col in columns:
                    value = getattr(row, col)
                    if not value:
                        continue
                    if is_fernet_token(value):
                        try:
                            # rotate(): descriptografa com qualquer chave da lista e
                            # recriptografa com a primeira (FERNET_KEY).
                            new_value = keyring.rotate(value.encode()).decode()
                            rotated += 1
                        except InvalidToken:
                            failed += 1
                            print(f"  FALHA: {model.__tablename__}.{col} (id={row_id(row)}) não abre com nenhuma chave")
                            continue
                    else:
                        # Texto puro legado: passa a ser criptografado.
                        new_value = keyring.encrypt(value.encode()).decode()
                        plaintext += 1
                    setattr(row, col, new_value)

        print(f"Campos recriptografados: {rotated}")
        print(f"Campos em texto puro criptografados: {plaintext}")
        print(f"Falhas: {failed}")

        if failed:
            await db.rollback()
            sys.exit("Abortado: nenhuma alteração gravada. Informe a chave antiga em FERNET_OLD_KEYS ou LEGACY_JWT_SECRET.")
        if not apply:
            await db.rollback()
            print("Simulação concluída. Nada foi gravado. Rode com --apply para gravar.")
            return

        await db.commit()
        print("Migração gravada com sucesso.")


def row_id(row) -> str:
    return getattr(row, "id", None) or getattr(row, "version_id", None) or getattr(row, "user_id", "?")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recriptografa os campos sensíveis com a FERNET_KEY atual.")
    parser.add_argument("--apply", action="store_true", help="grava as alterações (sem isso, é só simulação)")
    asyncio.run(migrate(parser.parse_args().apply))
