# rebuild_search_index.py
# ---------------------------------------------------------------------------
# (Re)constrói o índice cego de busca por nome (ESCALA: busca no servidor).
#
# Quando rodar:
#   - depois da migração 20260930_scale_indexes, para indexar os pacientes que
#     já existiam (o prestart.py faz isso sozinho em todo deploy);
#   - com --all depois de TROCAR a SEARCH_INDEX_KEY (os tokens antigos deixam
#     de bater com as buscas).
#
# Uso (dentro do container da API):
#   python rebuild_search_index.py          -> só pacientes ainda sem tokens
#   python rebuild_search_index.py --all    -> apaga e refaz o índice inteiro
#
# É seguro rodar quantas vezes quiser: o índice é derivado do nome, que
# continua guardado (criptografado) em patients.full_name. Grava em lotes:
# se parar no meio, rodar de novo completa o que faltou.
# ---------------------------------------------------------------------------
import argparse
import asyncio

from sqlalchemy import delete, exists, select

from crypto import is_fernet_token, load_fernet, load_search_key, name_search_tokens
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
        stmt = select(Patient).where(~exists().where(PatientSearchToken.patient_id == Patient.id))
        patients = (await db.execute(stmt)).scalars().all()

        for index, patient in enumerate(patients, start=1):
            name = patient.full_name or ""
            if is_fernet_token(name):  # dados legados ainda em texto puro também são indexados
                name = fernet.decrypt(name.encode("utf-8")).decode("utf-8")
            db.add_all([
                PatientSearchToken(patient_id=patient.id, owner_id=patient.owner_id, token=token)
                for token in name_search_tokens(search_key, patient.owner_id, name)
            ])
            if index % BATCH_SIZE == 0:
                await db.commit()
        await db.commit()
    return len(patients)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconstrói o índice de busca por nome dos pacientes.")
    parser.add_argument("--all", action="store_true", help="apaga e refaz o índice inteiro (após trocar a chave)")
    count = asyncio.run(rebuild(full=parser.parse_args().all))
    print(f"Pacientes indexados: {count}")
