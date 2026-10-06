# rebuild_search_index.py
# ---------------------------------------------------------------------------
# (Re)constrói o índice cego de busca por nome (ESCALA: busca no servidor) e,
# desde a análise de UI (P5), por telefone e CPF.
#
# Quando rodar:
#   - depois da migração 20260930_scale_indexes, para indexar os pacientes que
#     já existiam (o prestart.py faz isso sozinho em todo deploy);
#   - depois da migração 20261006_productivity: os pacientes com o índice numa
#     versão antiga (search_index_version < SEARCH_INDEX_VERSION, ou NULL) são
#     reindexados com telefone e CPF (também automático no prestart);
#   - com --all depois de TROCAR a SEARCH_INDEX_KEY (os tokens antigos deixam
#     de bater com as buscas).
#
# Uso (dentro do container da API):
#   python rebuild_search_index.py          -> só pacientes sem índice ou com índice antigo
#   python rebuild_search_index.py --all    -> apaga e refaz o índice inteiro
#
# É seguro rodar quantas vezes quiser: o índice é derivado do nome, do telefone
# e do CPF, que continuam guardados (criptografados) na ficha. Grava em lotes:
# se parar no meio, rodar de novo completa o que faltou.
# ---------------------------------------------------------------------------
import argparse
import asyncio

from sqlalchemy import delete, or_, select

from crypto import (
    SEARCH_INDEX_VERSION, contact_search_tokens, is_fernet_token, load_fernet, load_search_key, name_search_tokens,
)
from database import AsyncSessionLocal
from environment import get_environment
from models import Patient, PatientSearchToken

BATCH_SIZE = 500


async def rebuild(full: bool = False, session_factory=None) -> int:
    # SEGURANÇA (varredura, item 5): mesma regra do servidor. Sem ENVIRONMENT,
    # vale produção, e as chaves passam a ser obrigatórias. Ver environment.py.
    environment = get_environment()
    fernet = load_fernet(environment)
    search_key = load_search_key(environment)
    factory = session_factory or AsyncSessionLocal

    async with factory() as db:
        if full:
            await db.execute(delete(PatientSearchToken))
            await db.commit()
            stmt = select(Patient)
        else:
            # P5: a versão do índice diz o que já foi indexado. Antes o critério
            # era "paciente sem nenhum token", que não pega quem só tem o nome.
            stmt = select(Patient).where(or_(Patient.search_index_version.is_(None),
                                             Patient.search_index_version < SEARCH_INDEX_VERSION))
        patients = (await db.execute(stmt)).scalars().all()

        def plain(value):
            value = value or ""
            if is_fernet_token(value):  # dados legados ainda em texto puro também são indexados
                return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
            return value

        for index, patient in enumerate(patients, start=1):
            tokens = name_search_tokens(search_key, patient.owner_id, plain(patient.full_name))
            # P5: telefone e CPF do paciente e do responsável (mesma regra do servidor).
            tokens |= contact_search_tokens(search_key, patient.owner_id,
                                            phones=(plain(patient.phone), plain(patient.guardian_phone)),
                                            cpfs=(plain(patient.cpf), plain(patient.guardian_cpf)))
            await db.execute(delete(PatientSearchToken).where(PatientSearchToken.patient_id == patient.id))
            db.add_all([PatientSearchToken(patient_id=patient.id, owner_id=patient.owner_id, token=token)
                        for token in tokens])
            patient.search_index_version = SEARCH_INDEX_VERSION
            if index % BATCH_SIZE == 0:
                await db.commit()
        await db.commit()
    return len(patients)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconstrói o índice de busca (nome, telefone e CPF) dos pacientes.")
    parser.add_argument("--all", action="store_true", help="apaga e refaz o índice inteiro (após trocar a chave)")
    count = asyncio.run(rebuild(full=parser.parse_args().all))
    print(f"Pacientes indexados: {count}")
