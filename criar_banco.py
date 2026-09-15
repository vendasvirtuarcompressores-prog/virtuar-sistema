import os
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

# Configuração de caminhos
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "compras_nfe.db"
PASTA_XMLS = BASE_DIR / "xmls"  # Coloque seus arquivos .xml aqui


def inicializar_banco():
    """Cria a estrutura das tabelas no SQLite caso não existam."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fornecedores (
        cnpj TEXT PRIMARY KEY,
        nome TEXT,
        uf TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notas_fiscais (
        chave_nfe TEXT PRIMARY KEY,
        numero_nf TEXT,
        data_emissao TEXT,
        cnpj_fornecedor TEXT,
        valor_total REAL,
        FOREIGN KEY (cnpj_fornecedor) REFERENCES fornecedores (cnpj)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS itens_nota (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chave_nfe TEXT,
        codigo_prod TEXT,
        descricao TEXT,
        ean TEXT,
        ncm TEXT,
        quantidade REAL,
        valor_unitario REAL,
        valor_total REAL,
        FOREIGN KEY (chave_nfe) REFERENCES notas_fiscais (chave_nfe)
    )
    """)

    conn.commit()
    conn.close()


def processar_xml(caminho_xml):
    """Lê um arquivo XML de NF-e e extrai os dados."""
    tree = ET.parse(caminho_xml)
    root = tree.getroot()

    # Define o namespace da NFe
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

    # Localizar nó infNFe
    inf_nfe = root.find(".//nfe:infNFe", ns)
    if inf_nfe is None:
        return None

    chave_nfe = inf_nfe.attrib.get("Id", "").replace("NFe", "")

    # Dados da Ide (Identificação da NF)
    ide = inf_nfe.find("nfe:ide", ns)
    numero_nf = ide.findtext("nfe:nNF", "", ns)
    data_emissao = ide.findtext("nfe:dhEmi", "", ns)[:10]  # Pega YYYY-MM-DD

    # Dados do Emitente
    emit = inf_nfe.find("nfe:emit", ns)
    cnpj_fornecedor = emit.findtext("nfe:CNPJ", "", ns)
    nome_fornecedor = emit.findtext("nfe:xNome", "", ns)
    uf_fornecedor = emit.find("nfe:enderEmit", ns).findtext("nfe:UF", "", ns)

    # Total da Nota
    total = inf_nfe.find(".//nfe:ICMSTot", ns)
    valor_total_nf = float(total.findtext("nfe:vNF", "0.0", ns))

    # Itens da Nota
    itens = []
    for det in inf_nfe.findall("nfe:det", ns):
        prod = det.find("nfe:prod", ns)
        itens.append(
            {
                "codigo_prod": prod.findtext("nfe:cProd", "", ns),
                "descricao": prod.findtext("nfe:xProd", "", ns),
                "ean": prod.findtext("nfe:cEAN", "", ns),
                "ncm": prod.findtext("nfe:NCM", "", ns),
                "quantidade": float(prod.findtext("nfe:qCom", "0.0", ns)),
                "valor_unitario": float(prod.findtext("nfe:vUnCom", "0.0", ns)),
                "valor_total": float(prod.findtext("nfe:vProd", "0.0", ns)),
            }
        )

    return {
        "chave_nfe": chave_nfe,
        "numero_nf": numero_nf,
        "data_emissao": data_emissao,
        "fornecedor": {
            "cnpj": cnpj_fornecedor,
            "nome": nome_fornecedor,
            "uf": uf_fornecedor,
        },
        "valor_total_nf": valor_total_nf,
        "itens": itens,
    }


def salvar_no_banco(dados):
    """Insere ou atualiza os dados no banco de dados SQLite."""
    if not dados:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Insere Fornecedor
    cursor.execute(
        """
        INSERT OR IGNORE INTO fornecedores (cnpj, nome, uf)
        VALUES (?, ?, ?)
    """,
        (
            dados["fornecedor"]["cnpj"],
            dados["fornecedor"]["nome"],
            dados["fornecedor"]["uf"],
        ),
    )

    # Insere Nota Fiscal
    cursor.execute(
        """
        INSERT OR REPLACE INTO notas_fiscais (chave_nfe, numero_nf, data_emissao, cnpj_fornecedor, valor_total)
        VALUES (?, ?, ?, ?, ?)
    """,
        (
            dados["chave_nfe"],
            dados["numero_nf"],
            dados["data_emissao"],
            dados["fornecedor"]["cnpj"],
            dados["valor_total_nf"],
        ),
    )

    # Limpa itens anteriores da mesma nota para evitar duplicatas em re-processamento
    cursor.execute(
        "DELETE FROM itens_nota WHERE chave_nfe = ?", (dados["chave_nfe"],)
    )

    # Insere Itens
    for item in dados["itens"]:
        cursor.execute(
            """
            INSERT INTO itens_nota (chave_nfe, codigo_prod, descricao, ean, ncm, quantidade, valor_unitario, valor_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                dados["chave_nfe"],
                item["codigo_prod"],
                item["descricao"],
                item["ean"],
                item["ncm"],
                item["quantidade"],
                item["valor_unitario"],
                item["valor_total"],
            ),
        )

    conn.commit()
    conn.close()


def importar_todos_xmls():
    """Percorre a pasta de XMLs e importa um por um."""
    inicializar_banco()

    if not PASTA_XMLS.exists():
        PASTA_XMLS.mkdir(parents=True, exist_ok=True)
        print(
            f"Pasta '{PASTA_XMLS}' criada. Coloque seus arquivos XML nela e execute novamente."
        )
        return

    arquivos = list(PASTA_XMLS.glob("*.xml"))
    print(f"Encontrados {len(arquivos)} arquivos XML para processar.")

    for idx, arquivo in enumerate(arquivos, start=1):
        try:
            dados = processar_xml(arquivo)
            salvar_no_banco(dados)
            print(f"[{idx}/{len(arquivos)}] Processado: {arquivo.name}")
        except Exception as e:
            print(f"Erro ao processar {arquivo.name}: {e}")

    print("Importação concluída com sucesso!")


if __name__ == "__main__":
    importar_todos_xmls()