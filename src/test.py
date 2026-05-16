import requests
import os

GROBID_URL = "http://localhost:8070/api/processFulltextDocument"
HEALTH_URL = "http://localhost:8070/api/isalive"

def check_grobid():
    try:
        r = requests.get(HEALTH_URL, timeout=5)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError("GROBID service is not available") from e

def pdf_to_tei(pdf_path: str) -> str:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    check_grobid()  # 可选，确保服务就绪

    params = {
        "consolidateHeader": "1",
        "processFormula": "true"   # 如需提取公式
    }
    with open(pdf_path, 'rb') as f:
         files = {'input': f}
         resp = requests.post(GROBID_URL, files=files, params=params, timeout=120)
         resp.raise_for_status()
    return resp.text  # 或者 resp.content.decode('utf-8')

# 调用时使用原始字符串或正斜杠
tei = pdf_to_tei(r"E:\project\RAG_TEACHER\MODULAR-RAG-MCP-SERVER-main\MODULAR-RAG-MCP-SERVER-main\papers\research\Nonreciprocal Interactions Reshape Topological Defect Annihilation.pdf")
print("TEI XML length:", len(tei))
