from fastapi import FastAPI, Request
from web3 import Web3
from typing import Optional
import os
import requests

app = FastAPI()

# ================== CONFIGURACIÓN BLOCKCHAIN ==================

RPC_URL = "https://eth-sepolia.g.alchemy.com/v2/dSfxKDUR1T06yEdb3pPDzA94HzMYgs_e"

CONTRACT_ADDRESS = Web3.to_checksum_address(
    "0x50268060AAd99FEdB907080Ec8138E9f4C5A0e2d"
)

PRIVATE_KEY = os.environ.get(
    "PRIVATE_KEY",
    "945fa986313ff16e10e94b6f71ff8d6f4624cd29f1e02cfd73113d81cdd2b4a0"
)

CHAIN_ID = 11155111  # Sepolia

ABI_JSON = [
    {
        "inputs": [
            {"internalType": "string", "name": "deviceId", "type": "string"},
            {"internalType": "int16", "name": "temperatureTimes10", "type": "int16"},
            {"internalType": "uint16", "name": "humidityTimes10", "type": "uint16"},
            {"internalType": "uint256", "name": "timestampMs", "type": "uint256"},
            {"internalType": "string", "name": "cid", "type": "string"},
        ],
        "name": "storeReading",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# ================== CONFIGURACIÓN PINATA ==================

PINATA_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySW5mb3JtYXRpb24iOnsiaWQiOiJhNTNlZGVhMS0zM2Q4LTRhNTMtYTVjNS05ZWQ3NTBiNTMyMTEiLCJlbWFpbCI6InViZXJ0aWNvbWlndWVsQGdtYWlsLmNvbSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlLCJwaW5fcG9saWN5Ijp7InJlZ2lvbnMiOlt7ImRlc2lyZWRSZXBsaWNhdGlvbkNvdW50IjoxLCJpZCI6IkZSQTEifSx7ImRlc2lyZWRSZXBsaWNhdGlvbkNvdW50IjoxLCJpZCI6Ik5ZQzEifV0sInZlcnNpb24iOjF9LCJtZmFfZW5hYmxlZCI6ZmFsc2UsInN0YXR1cyI6IkFDVElWRSJ9LCJhdXRoZW50aWNhdGlvblR5cGUiOiJzY29wZWRLZXkiLCJzY29wZWRLZXlLZXkiOiI4ZjhmNzU5YzM3ZmRkMzU4OWFlZSIsInNjb3BlZEtleVNlY3JldCI6Ijk3NzI5NTIxODQyYmI1MWM1ZTczY2I3M2YyOWY5YjZjMzFlZjczMGFjNTEzNDBlNjZhYjBiNDI4M2MwOGRkZmMiLCJleHAiOjE3OTQ5NTg1NjR9.VLhFoJGb1h9D85MZ2E2j70Ckk7HUalvpvk-x2yonRjk"
)

PINATA_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"

# ================== INICIALIZACIÓN WEB3 ==================

w3 = Web3(Web3.HTTPProvider(RPC_URL))

if not w3.is_connected():
    raise RuntimeError("No se pudo conectar a Sepolia. Revisa RPC_URL / Internet.")

account = w3.eth.account.from_key(PRIVATE_KEY)
print("[INFO] Relayer usando cuenta:", account.address)

contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=ABI_JSON)

# ================== ALMACENAMIENTO LOCAL (GET) ==================
lecturas_local = []


@app.get("/")
def root():
    return {"status": "ok", "message": "Relayer funcionando"}


# ================== FUNCIÓN PARA SUBIR A PINATA ==================

def subir_a_pinata(payload: dict) -> Optional[str]:
    if not PINATA_JWT:
        print("[WARN] PINATA_JWT vacío, no se subirá a IPFS.")
        return None

    headers = {
        "Authorization": f"Bearer {PINATA_JWT}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(PINATA_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        res = r.json()
        cid = res.get("IpfsHash")
        print("[INFO] Subido a Pinata, CID:", cid)
        return cid
    except Exception as e:
        print("[ERROR] Error subiendo a Pinata:", e)
        return None


# ================== ENDPOINT POST (RECIBIR ESP32) ==================

@app.post("/api/lecturas")
async def recibir_lectura(req: Request):
    data = await req.json()
    print("[DEBUG] Payload recibido:", data)

    device_id = data.get("device_id", "unknown-device")
    temp_c = float(data["temperature"])
    hum = float(data["humidity"])
    timestamp_ms = int(data["timestamp_ms"])

    # Guardar localmente para poder consultarlo con GET
    lecturas_local.append({
        "device_id": device_id,
        "temperature": temp_c,
        "humidity": hum,
        "timestamp_ms": timestamp_ms
    })

    # Escalar para contrato
    temp_times10 = int(round(temp_c * 10))
    hum_times10 = int(round(hum * 10))

    # Subir JSON completo a Pinata
    pinata_payload = {
        "device_id": device_id,
        "temperature_c": temp_c,
        "humidity_percent": hum,
        "timestamp_ms": timestamp_ms,
    }
    cid = subir_a_pinata(pinata_payload) or ""

    # Construir transacción
    nonce = w3.eth.get_transaction_count(account.address)

    tx = contract.functions.storeReading(
        device_id, temp_times10, hum_times10, timestamp_ms, cid
    ).build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "gas": 300000,
            "gasPrice": w3.eth.gas_price,
            "chainId": CHAIN_ID,
        }
    )

    signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)

    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    print("[INFO] Tx enviada:", tx_hash.hex())
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print("[INFO] Tx minada en bloque:", receipt.blockNumber)

    return {
        "status": "ok",
        "tx_hash": tx_hash.hex(),
        "block": receipt.blockNumber,
        "cid": cid,
    }


# ================== ENDPOINT GET (MOSTRAR LECTURAS) ==================

@app.get("/api/lecturas")
def obtener_lecturas():
    return lecturas_local
